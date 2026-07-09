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
    if (/\/api\/users\/7\/servers\/\d+$/.test(url) && method === "PATCH") {
      const body = JSON.parse(init?.body as string);
      servers = servers.map((s) =>
        url.endsWith(`/${s.id}`) ? { ...s, ...body } : s,
      );
      return json(servers.find((s) => url.endsWith(`/${s.id}`)));
    }
    if (/\/api\/users\/7\/servers\/\d+$/.test(url) && method === "DELETE") {
      servers = servers.filter((s) => !url.endsWith(`/${s.id}`));
      return json({ detail: "Server record removed." });
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
    await userEvent.type(screen.getByLabelText(/server name/i), "coder box");
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
    expect(JSON.parse((createCall![1] as RequestInit).body as string)).toMatchObject(
      {
        template_id: 1,
        name: "coder box",
        install_pubkey: true,
        pubkey_users: "john-doe",
      },
    );
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

  it("removes a server record after confirmation", async () => {
    stubServers([makeServer()]);
    render(<UserServersPanel userId={7} canCreate={false} canDelete />);

    await screen.findByText("coder box - 10.0.7.42");
    await userEvent.click(screen.getByRole("button", { name: /^remove$/i }));
    await userEvent.click(
      screen.getByRole("button", { name: /confirm remove record/i }),
    );
    expect(await screen.findByText(/no servers/i)).toBeInTheDocument();
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
