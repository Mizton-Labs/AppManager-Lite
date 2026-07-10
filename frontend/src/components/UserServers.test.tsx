import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserServersPanel, quotaLevel } from "./UserServers";
import type { UserServer } from "../types";

function makeServer(overrides: Partial<UserServer> = {}): UserServer {
  return {
    id: 1,
    user_id: 7,
    name: "coder box",
    hostname: "coder-box",
    template_id: 1,
    template_name: "Debian Coder",
    vmid: 120,
    node: "pve1",
    kind: "lxc",
    ip_address: "10.0.7.42",
    cpus: 2,
    memory_gb: 4,
    disk_gb: 20,
    admin_modified: false,
    status: "created",
    last_log: "[t] Server created successfully",
    deletion_requested_at: "",
    deletion_pending: false,
    deletion_failed: false,
    deletion_error: "",
    created_at: "2026-07-08 00:00:00",
    ...overrides,
  };
}

function stubServers(
  initial: UserServer[] = [],
  usage: unknown = {
    unlimited: false,
    servers: { used: 1, limit: 3 },
    cpus: { used: 2, limit: 12 },
    memory_gb: { used: 4, limit: 24 },
    disk_gb: { used: 20, limit: 200 },
  },
) {
  let servers = [...initial];
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const json = (payload: unknown, status = 200) =>
      ({ ok: status < 400, status, json: async () => payload }) as Response;

    if (url.endsWith("/api/account/server-templates")) {
      return json([
        { id: 1, name: "Debian Coder", kind: "lxc" },
        { id: 2, name: "Windows VM", kind: "vm" },
      ]);
    }
    if (/\/api\/users\/7\/servers\/usage$/.test(url) && method === "GET") {
      return json(usage);
    }
    if (/\/api\/users\/7\/servers$/.test(url) && method === "GET") {
      return json(servers);
    }
    if (/\/api\/users\/7\/servers$/.test(url) && method === "POST") {
      const body = JSON.parse(init?.body as string);
      const created = makeServer({
        id: servers.length + 1,
        name: body.name,
        kind: body.template_id === 2 ? "vm" : "lxc",
        ip_address: body.template_id === 2 ? "" : "10.0.7.42",
      });
      servers = [...servers, created];
      return json(created, 201);
    }
    if (/\/api\/users\/7\/servers\/\d+\/cancel-deletion$/.test(url) &&
        method === "POST") {
      servers = servers.map((s) =>
        url.includes(`/${s.id}/cancel-deletion`)
          ? { ...s, deletion_pending: false, deletion_requested_at: "" }
          : s,
      );
      return json(
        servers.find((s) => url.includes(`/${s.id}/cancel-deletion`)),
      );
    }
    if (/\/api\/users\/7\/servers\/\d+\/force-remove$/.test(url) &&
        method === "POST") {
      servers = servers.filter(
        (s) => !url.includes(`/${s.id}/force-remove`),
      );
      return json({ detail: "Server record removed. The guest was destroyed." });
    }
    if (/\/api\/users\/7\/servers\/\d+$/.test(url) && method === "PATCH") {
      const body = JSON.parse(init?.body as string);
      servers = servers.map((s) =>
        url.endsWith(`/${s.id}`) ? { ...s, ...body } : s,
      );
      return json(servers.find((s) => url.endsWith(`/${s.id}`)));
    }
    if (/\/api\/users\/7\/servers\/\d+$/.test(url) && method === "DELETE") {
      // Deferred deletion: the server is marked pending, not removed.
      servers = servers.map((s) =>
        url.endsWith(`/${s.id}`)
          ? {
              ...s,
              deletion_pending: true,
              deletion_requested_at: "2026-07-08 00:00:00",
            }
          : s,
      );
      return json(servers.find((s) => url.endsWith(`/${s.id}`)));
    }
    return json({ detail: `unexpected ${method} ${url}` }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("UserServersPanel", () => {
  it("lists servers as NAME - IP cards", async () => {
    stubServers([makeServer()]);
    render(
      <UserServersPanel userId={7} canCreate={false} canDelete={false} />,
    );
    expect(
      await screen.findByText("coder box - 10.0.7.42"),
    ).toBeInTheDocument();
    expect(screen.getByText("LXC")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add server/i })).toBeNull();
  });

  it("creates a server with pubkey users and shows success", async () => {
    const fetchMock = stubServers();
    render(
      <UserServersPanel
        userId={7}
        canCreate
        canDelete
        defaultPubkeyUser="john-doe"
      />,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: /add server/i }),
    );
    // Pubkey users prefilled with the derived user-id.
    expect(screen.getByLabelText(/os users receiving the key/i)).toHaveValue(
      "john-doe",
    );
    // The static name prefix "<template-slug>-<owner-id>-" is shown; the input
    // is only the suffix.
    expect(screen.getByText("debian-coder-john-doe-")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/server name/i), "coder box");
    // The live preview shows the composed full name.
    expect(
      screen.getByText(/debian-coder-john-doe-coder box/),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /^create server$/i }),
    );

    expect(
      await screen.findByText(/created successfully/i),
    ).toBeInTheDocument();
    const createCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        /\/api\/users\/7\/servers$/.test(String(url)) &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    // The request carries only the suffix; the backend composes the full name.
    expect(JSON.parse((createCall![1] as RequestInit).body as string)).toMatchObject(
      {
        template_id: 1,
        name: "coder box",
        install_pubkey: true,
        pubkey_users: "john-doe",
      },
    );
  });

  it("shows remaining suffix characters and surfaces a name conflict", async () => {
    const fetchMock = stubServers();
    // Make the create endpoint reject with a 409 conflict.
    fetchMock.mockImplementation(async (input: string, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const json = (payload: unknown, status = 200) =>
        ({ ok: status < 400, status, json: async () => payload }) as Response;
      if (url.endsWith("/api/account/server-templates")) {
        return json([{ id: 1, name: "Debian Coder", kind: "lxc" }]);
      }
      if (/\/api\/users\/7\/servers\/usage$/.test(url)) {
        return json({
          unlimited: false,
          servers: { used: 0, limit: 3 },
          cpus: { used: 0, limit: 12 },
          memory_gb: { used: 0, limit: 24 },
          disk_gb: { used: 0, limit: 200 },
        });
      }
      if (/\/api\/users\/7\/servers$/.test(url) && method === "GET") {
        return json([]);
      }
      if (/\/api\/users\/7\/servers$/.test(url) && method === "POST") {
        return json(
          { detail: "A server named 'X' already exists. Choose a different name suffix." },
          409,
        );
      }
      return json({ detail: "unexpected" }, 500);
    });
    render(
      <UserServersPanel
        userId={7}
        canCreate
        canDelete
        defaultPubkeyUser="john-doe"
      />,
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /add server/i }),
    );
    // Prefix "debian-coder-john-doe-" is 22 chars -> 41 suffix chars available.
    expect(screen.getByText(/41 of 41 characters left/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/server name/i), "dup");
    expect(screen.getByText(/38 of 41 characters left/i)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /^create server$/i }),
    );
    // The backend's global-uniqueness 409 is shown inline.
    expect(
      await screen.findByText(/choose a different name suffix/i),
    ).toBeInTheDocument();
  });

  it("guides VM servers to manual IP entry and saves it", async () => {
    stubServers([
      makeServer({ id: 3, name: "win vm", kind: "vm", ip_address: "" }),
    ]);
    render(<UserServersPanel userId={7} canCreate canDelete />);

    expect(await screen.findByText("win vm")).toBeInTheDocument();
    const ipInput = screen.getByLabelText(/server ip address/i);
    await userEvent.type(ipInput, "10.1.2.3");
    await userEvent.click(screen.getByRole("button", { name: /save ip/i }));

    expect(await screen.findByText("win vm - 10.1.2.3")).toBeInTheDocument();
  });

  it("shows the failure log behind a toggle for failed servers", async () => {
    stubServers([
      makeServer({
        id: 4,
        name: "doomed",
        status: "failed",
        ip_address: "",
        last_log: "[t] ERROR: clone: task failed",
      }),
    ]);
    render(<UserServersPanel userId={7} canCreate={false} canDelete={false} />);

    expect(await screen.findByText("failed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /view log/i }));
    expect(screen.getByText(/clone: task failed/i)).toBeInTheDocument();
  });

  it("schedules a deferred deletion after confirmation", async () => {
    stubServers([makeServer()]);
    render(<UserServersPanel userId={7} canCreate={false} canDelete />);

    await screen.findByText("coder box - 10.0.7.42");
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    // Confirmation warns about the permanent 24h-grace deletion.
    expect(
      screen.getByText(/enters a 24-hour grace period/i),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /yes, schedule deletion/i }),
    );
    // The server stays listed, now marked pending with a cancel action.
    expect(await screen.findByText(/deletion pending/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /cancel deletion/i }),
    ).toBeInTheDocument();
  });

  it("cancels a pending deletion", async () => {
    stubServers([
      makeServer({
        deletion_pending: true,
        deletion_requested_at: "2999-01-01 00:00:00",
      }),
    ]);
    render(<UserServersPanel userId={7} canCreate={false} canDelete />);

    expect(await screen.findByText(/deletion pending/i)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /cancel deletion/i }),
    );
    // After cancelling, the pending badge is gone and Delete is available.
    expect(await screen.findByRole("button", { name: /^delete$/i })).toBeInTheDocument();
    expect(screen.queryByText(/deletion pending/i)).toBeNull();
  });

  it("shows the destroy error and force-remove only to admins", async () => {
    stubServers([
      makeServer({
        deletion_pending: true,
        deletion_failed: true,
        deletion_requested_at: "2026-07-08 00:00:00",
        deletion_error: "destroy: task failed (lock)",
      }),
    ]);
    render(<UserServersPanel userId={7} canCreate={false} canDelete isAdmin />);

    expect(await screen.findByText(/deletion failed/i)).toBeInTheDocument();
    expect(screen.getByText(/destroy: task failed \(lock\)/i)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /^force remove$/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /confirm force remove/i }),
    );
    expect(await screen.findByText(/no servers/i)).toBeInTheDocument();
  });

  it("hides force-remove and the error from non-admins", async () => {
    // A failed-destroy row would not normally be sent to a non-admin, but the
    // UI must also gate the admin-only affordances defensively.
    stubServers([
      makeServer({
        deletion_pending: true,
        deletion_failed: true,
        deletion_requested_at: "2026-07-08 00:00:00",
        deletion_error: "",
      }),
    ]);
    render(<UserServersPanel userId={7} canCreate={false} canDelete />);
    await screen.findByText(/deletion failed/i);
    expect(screen.queryByRole("button", { name: /force remove/i })).toBeNull();
  });

  it("shows resource quota bars in the create form", async () => {
    stubServers([], {
      unlimited: false,
      servers: { used: 1, limit: 3 },
      cpus: { used: 6, limit: 12 },
      memory_gb: { used: 22, limit: 24 },
      disk_gb: { used: 20, limit: 200 },
    });
    render(<UserServersPanel userId={7} canCreate canDelete />);
    await userEvent.click(
      await screen.findByRole("button", { name: /add server/i }),
    );
    // Bars are labelled progressbars with used/limit accessible names.
    expect(
      await screen.findByRole("progressbar", { name: /Servers: 1 of 3 used/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("progressbar", { name: /Memory: 22 of 24 GB used/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("6/12")).toBeInTheDocument();
  });

  it("renders an over-limit bar as red and clamps the fill width", async () => {
    stubServers([], {
      unlimited: false,
      servers: { used: 1, limit: 3 },
      cpus: { used: 14, limit: 12 }, // over limit
      memory_gb: { used: 4, limit: 24 },
      disk_gb: { used: 20, limit: 200 },
    });
    render(<UserServersPanel userId={7} canCreate canDelete />);
    await userEvent.click(
      await screen.findByRole("button", { name: /add server/i }),
    );
    const cpuBar = await screen.findByRole("progressbar", {
      name: /CPUs: 14 of 12 used/i,
    });
    const fill = cpuBar.querySelector(".quota-bar-fill");
    expect(fill).toHaveClass("quota-full");
    expect((fill as HTMLElement).style.width).toBe("100%");
  });

  it("keeps the form usable when usage fails to load", async () => {
    // usage route returns 500 -> QuotaBars fails quietly (renders null).
    const fetchMock = stubServers([], undefined);
    // Override: make the usage endpoint fail.
    fetchMock.mockImplementation(async (input: string, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const json = (payload: unknown, status = 200) =>
        ({ ok: status < 400, status, json: async () => payload }) as Response;
      if (url.endsWith("/api/account/server-templates")) {
        return json([{ id: 1, name: "Debian Coder", kind: "lxc" }]);
      }
      if (/\/api\/users\/7\/servers\/usage$/.test(url)) {
        return json({ detail: "boom" }, 500);
      }
      if (/\/api\/users\/7\/servers$/.test(url) && method === "GET") {
        return json([]);
      }
      return json({ detail: "unexpected" }, 500);
    });
    render(<UserServersPanel userId={7} canCreate canDelete />);
    await userEvent.click(
      await screen.findByRole("button", { name: /add server/i }),
    );
    // Form still renders (template + name field), no crash, no bars.
    expect(await screen.findByLabelText(/server name/i)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("shows an admin-exempt note instead of bars when acting as admin", async () => {
    stubServers([], {
      unlimited: false,
      servers: { used: 3, limit: 3 },
      cpus: { used: 12, limit: 12 },
      memory_gb: { used: 24, limit: 24 },
      disk_gb: { used: 200, limit: 200 },
    });
    render(<UserServersPanel userId={7} canCreate canDelete isAdmin />);
    await userEvent.click(
      await screen.findByRole("button", { name: /add server/i }),
    );
    // Even though the target is at 3/3, an admin actor is not quota-gated.
    expect(
      await screen.findByText(/per-user resource limits are not enforced/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("shows a no-limits note for administrator target users", async () => {
    stubServers([], {
      unlimited: true,
      servers: { used: 2, limit: 0 },
      cpus: { used: 8, limit: 0 },
      memory_gb: { used: 16, limit: 0 },
      disk_gb: { used: 80, limit: 0 },
    });
    render(<UserServersPanel userId={7} canCreate canDelete />);
    await userEvent.click(
      await screen.findByRole("button", { name: /add server/i }),
    );
    expect(await screen.findByText(/no resource limits/i)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("always shows a resource line, marking unrecorded specs", async () => {
    stubServers([
      makeServer({ id: 9, name: "ref", cpus: 0, memory_gb: 0, disk_gb: 0,
        status: "reference", vmid: null }),
    ]);
    render(<UserServersPanel userId={7} canCreate={false} canDelete={false} />);
    expect(await screen.findByText(/Resources: not recorded/i)).toBeInTheDocument();
  });

  it("shows the resource editor only when editing is allowed", async () => {
    stubServers([makeServer()]);
    render(
      <UserServersPanel
        userId={7}
        canCreate={false}
        canDelete={false}
        allowResourceEdit
      />,
    );
    await screen.findByText("coder box - 10.0.7.42");
    await userEvent.click(screen.getByRole("button", { name: /^Edit$/ }));
    expect(screen.getByLabelText("CPUs")).toBeInTheDocument();
    expect(screen.getByLabelText("Memory (GB)")).toBeInTheDocument();
    expect(screen.getByLabelText("Disk (GB)")).toBeInTheDocument();
  });

  it("hides the editor for admin-managed or non-eligible servers", async () => {
    stubServers([makeServer({ admin_modified: true })]);
    render(
      <UserServersPanel
        userId={7}
        canCreate={false}
        canDelete={false}
        allowResourceEdit
      />,
    );
    await screen.findByText("coder box - 10.0.7.42");
    expect(screen.queryByRole("button", { name: /^Edit$/ })).toBeNull();
  });

  it("hides the editor entirely when editing is not allowed", async () => {
    stubServers([makeServer()]);
    render(<UserServersPanel userId={7} canCreate={false} canDelete={false} />);
    await screen.findByText("coder box - 10.0.7.42");
    expect(screen.queryByRole("button", { name: /^Edit$/ })).toBeNull();
  });

  it("saves changed resources via the update endpoint", async () => {
    const fetchMock = stubServers([makeServer()]);
    render(
      <UserServersPanel
        userId={7}
        canCreate={false}
        canDelete={false}
        allowResourceEdit
      />,
    );
    await screen.findByText("coder box - 10.0.7.42");
    await userEvent.click(screen.getByRole("button", { name: /^Edit$/ }));
    const cpus = screen.getByLabelText("CPUs");
    await userEvent.clear(cpus);
    await userEvent.type(cpus, "4");
    await userEvent.click(
      screen.getByRole("button", { name: /save resources/i }),
    );
    const patch = fetchMock.mock.calls.find(
      (c) =>
        /\/api\/users\/7\/servers\/1$/.test(String(c[0])) &&
        (c[1]?.method ?? "").toUpperCase() === "PATCH",
    );
    expect(patch).toBeTruthy();
    expect(JSON.parse(patch![1]!.body as string)).toMatchObject({ cpus: 4 });
  });

  it("surfaces a backend error when a resource change is rejected", async () => {
    let servers = [makeServer()];
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const json = (payload: unknown, status = 200) =>
        ({ ok: status < 400, status, json: async () => payload }) as Response;
      if (url.endsWith("/api/account/server-templates")) return json([]);
      if (/\/servers\/usage$/.test(url)) {
        return json({
          unlimited: false,
          servers: { used: 1, limit: 3 },
          cpus: { used: 2, limit: 12 },
          memory_gb: { used: 4, limit: 24 },
          disk_gb: { used: 20, limit: 200 },
        });
      }
      if (/\/api\/users\/7\/servers$/.test(url) && method === "GET")
        return json(servers);
      if (/\/api\/users\/7\/servers\/\d+$/.test(url) && method === "PATCH")
        return json({ detail: "disk can only be grown, not shrunk" }, 502);
      return json({ detail: "unexpected" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    void servers;

    render(
      <UserServersPanel
        userId={7}
        canCreate={false}
        canDelete={false}
        allowResourceEdit
      />,
    );
    await screen.findByText("coder box - 10.0.7.42");
    await userEvent.click(screen.getByRole("button", { name: /^Edit$/ }));
    await userEvent.click(
      screen.getByRole("button", { name: /save resources/i }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/disk can only be grown/i);
  });
});

describe("quotaLevel", () => {
  it("bands green <70, amber 70-90 inclusive, red >90", () => {
    expect(quotaLevel(0, 10)).toBe("ok");
    expect(quotaLevel(69, 100)).toBe("ok");
    expect(quotaLevel(70, 100)).toBe("warn"); // exactly 70 -> amber
    expect(quotaLevel(90, 100)).toBe("warn"); // exactly 90 -> amber
    expect(quotaLevel(91, 100)).toBe("full"); // >90 -> red
    expect(quotaLevel(14, 12)).toBe("full"); // over limit -> red
  });

  it("treats a zero/absent limit as ok (no division by zero)", () => {
    expect(quotaLevel(5, 0)).toBe("ok");
    expect(quotaLevel(0, 0)).toBe("ok");
  });
});
