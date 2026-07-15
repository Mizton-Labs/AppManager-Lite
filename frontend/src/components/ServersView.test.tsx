import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ServersView } from "./ServersView";
import type { ServerAccess, ServersOverview, UserServer } from "../types";
import { makeUser } from "../test/fixtures";

function server(overrides: Partial<UserServer> = {}): UserServer {
  return {
    id: 1,
    user_id: 7,
    name: "debian-coder-morris-a",
    hostname: "debian-coder-morris-a",
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
    last_log: "",
    deletion_requested_at: "",
    deletion_pending: false,
    deletion_failed: false,
    deletion_error: "",
    created_at: "2026-07-09 00:00:00",
    ...overrides,
  };
}

const STATS_OK = {
  available: true,
  detail: "",
  timeframe: "hour",
  points: [
    { time: 1000, cpu_pct: 25, mem: 1024, maxmem: 4096, disk: 500, maxdisk: 2000, netin: 10, netout: 20 },
    { time: 1060, cpu_pct: 50, mem: 2048, maxmem: 4096, disk: 600, maxdisk: 2000, netin: 30, netout: 40 },
  ],
};

const NO_ACCESS: ServerAccess = {
  can_create: false,
  reason: "",
  allow_resource_edit: false,
};

function stubServersView(
  overview: ServersOverview,
  stats: unknown = STATS_OK,
  access: ServerAccess = NO_ACCESS,
) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const json = (payload: unknown, status = 200) =>
      ({ ok: status < 400, status, json: async () => payload }) as Response;
    if (url.includes("/api/servers/overview")) return json(overview);
    if (url.endsWith("/api/account/server-access")) return json(access);
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
    if (/\/stats(\?|$)/.test(url)) return json(stats);
    if (/\/servers\/\d+\/reboot$/.test(url) && method === "POST") {
      return json(server());
    }
    if (/\/servers\/\d+\/reset-access$/.test(url) && method === "POST") {
      return json(server());
    }
    if (/\/users\/\d+\/servers\/\d+$/.test(url) && method === "PATCH") {
      const body = init?.body ? JSON.parse(init.body as string) : {};
      return json(server(body));
    }
    if (/\/users\/\d+\/servers\/\d+$/.test(url) && method === "DELETE") {
      return json(server({ deletion_pending: true }));
    }
    return json({ detail: "unexpected" }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The signed-in admin (sees and manages all owners). */
const ADMIN = makeUser({ id: 99, role: "admin", username: "admin@example.com" });
/** A signed-in self-service user (id 7 owns the sample servers). */
const SELF = makeUser({ id: 7, role: "user", self_service: true, user_id: "morris" });

afterEach(() => vi.unstubAllGlobals());

describe("ServersView", () => {
  it("groups servers by owner and shows resources + sparklines", async () => {
    stubServersView({
      is_admin: true,
      owners: [
        {
          user_id: 7,
          username: "morris@example.com",
          derived_user_id: "morris",
          servers: [server()],
        },
        {
          user_id: 8,
          username: "nadia@example.com",
          derived_user_id: "nadia",
          servers: [server({ id: 2, user_id: 8, name: "debian-coder-nadia-b" })],
        },
      ],
    });
    render(<ServersView currentUser={ADMIN} isAdmin />);

    // Owner group headers.
    expect(await screen.findByText(/morris@example.com/)).toBeInTheDocument();
    expect(screen.getByText(/nadia@example.com/)).toBeInTheDocument();
    // Server card resources (both servers share the same footprint).
    expect(
      screen.getAllByText(/2 CPU · 4 GB RAM · 20 GB disk/).length,
    ).toBe(2);
    // Charts render once stats load (CPU current value 50%).
    const cpuGroups = await screen.findAllByRole("group", { name: "CPU" });
    expect(cpuGroups.length).toBe(2); // one per server
    expect(screen.getAllByText("50%").length).toBe(2);
  });

  it("shows the Proxmox pool badge on a server card in the Servers section", async () => {
    stubServersView({
      is_admin: false,
      owners: [
        {
          user_id: 7,
          username: "morris@example.com",
          derived_user_id: "morris",
          servers: [server({ poolid: "team-morris" })],
        },
      ],
    });
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    expect(await screen.findByText("Pool: team-morris")).toBeInTheDocument();
  });

  it("re-fetches stats when the timeframe changes", async () => {
    const fetchMock = stubServersView({
      is_admin: false,
      owners: [
        {
          user_id: 7,
          username: "morris@example.com",
          derived_user_id: "morris",
          servers: [server()],
        },
      ],
    });
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    await screen.findByRole("group", { name: "CPU" });

    const statCallsBefore = fetchMock.mock.calls.filter(([u]) =>
      /\/stats\?timeframe=hour/.test(String(u)),
    ).length;
    expect(statCallsBefore).toBe(1);

    await userEvent.selectOptions(
      screen.getByLabelText(/stats timeframe/i),
      "day",
    );
    // A new stats request for the 'day' timeframe is issued.
    expect(
      fetchMock.mock.calls.some(([u]) =>
        /\/stats\?timeframe=day/.test(String(u)),
      ),
    ).toBe(true);
  });

  it("shows a per-server note when stats are unavailable", async () => {
    stubServersView(
      {
        is_admin: false,
        owners: [
          {
            user_id: 7,
            username: "morris@example.com",
            derived_user_id: "morris",
            servers: [server({ vmid: null })],
          },
        ],
      },
      { available: false, detail: "This server has no running guest to report stats for.", timeframe: "hour", points: [] },
    );
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    expect(
      await screen.findByText(/no running guest to report stats/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "CPU" })).toBeNull();
  });

  it("shows an empty state when there are no servers", async () => {
    stubServersView({
      is_admin: false,
      owners: [
        {
          user_id: 7,
          username: "morris@example.com",
          derived_user_id: "morris",
          servers: [],
        },
      ],
    });
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    expect(await screen.findByText(/no servers to show/i)).toBeInTheDocument();
  });

  it("shows an alert but still renders when the overview fails to load", async () => {
    const fetchMock = vi.fn(async (input: string) => {
      const url = String(input);
      const json = (payload: unknown, status = 200) =>
        ({ ok: status < 400, status, json: async () => payload }) as Response;
      if (url.includes("/api/servers/overview")) {
        return json({ detail: "nope" }, 500);
      }
      if (url.endsWith("/api/account/server-access")) return json(NO_ACCESS);
      return json({ detail: "unexpected" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    // The alert is shown and the heading still renders (no crash).
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /servers/i })).toBeInTheDocument();
  });

  it("notes when a server reports available stats but no data points", async () => {
    stubServersView(
      {
        is_admin: false,
        owners: [
          {
            user_id: 7,
            username: "morris@example.com",
            derived_user_id: "morris",
            servers: [server()],
          },
        ],
      },
      { available: true, detail: "", timeframe: "hour", points: [] },
    );
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    expect(
      await screen.findByText(/no usage data for this timeframe yet/i),
    ).toBeInTheDocument();
  });

  it("shows a Create server card behind the Add server button", async () => {
    stubServersView(
      {
        is_admin: false,
        owners: [
          {
            user_id: 7,
            username: "morris@example.com",
            derived_user_id: "morris",
            servers: [server()],
          },
        ],
      },
      STATS_OK,
      { can_create: true, reason: "", allow_resource_edit: true },
    );
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    // Collapsed by default: only the Add server button, no form yet.
    const addBtn = await screen.findByRole("button", { name: /add server/i });
    expect(
      screen.queryByRole("heading", { name: /create server/i }),
    ).toBeNull();
    await userEvent.click(addBtn);
    expect(
      await screen.findByRole("heading", { name: /create server/i }),
    ).toBeInTheDocument();
  });

  it("lets a self-service user edit and reboot their own servers", async () => {
    const fetchMock = stubServersView(
      {
        is_admin: false,
        owners: [
          {
            user_id: 7,
            username: "morris@example.com",
            derived_user_id: "morris",
            servers: [server()],
          },
        ],
      },
      STATS_OK,
      { can_create: true, reason: "", allow_resource_edit: true },
    );
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    await screen.findByText(/morris@example.com/);

    // Change resources -> PATCH.
    await userEvent.click(
      screen.getByRole("button", { name: /^Change resources$/ }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /save resources/i }),
    );
    expect(
      fetchMock.mock.calls.some(
        ([u, i]) =>
          /\/api\/users\/7\/servers\/1$/.test(String(u)) &&
          (i?.method ?? "").toUpperCase() === "PATCH",
      ),
    ).toBe(true);

    // Reboot -> confirm -> POST.
    await userEvent.click(screen.getByRole("button", { name: /^Reboot$/ }));
    await userEvent.click(
      screen.getByRole("button", { name: /^Confirm reboot$/ }),
    );
    expect(
      fetchMock.mock.calls.some(
        ([u, i]) =>
          /\/api\/users\/7\/servers\/1\/reboot$/.test(String(u)) &&
          (i?.method ?? "").toUpperCase() === "POST",
      ),
    ).toBe(true);

    // Reset access -> confirm -> POST for the owner's trusted set.
    await userEvent.click(
      screen.getByRole("button", { name: /^Reset access on servers$/ }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /^Confirm reset access$/ }),
    );
    expect(
      fetchMock.mock.calls.some(
        ([u, i]) =>
          /\/api\/users\/7\/servers\/1\/reset-access$/.test(String(u)) &&
          (i?.method ?? "").toUpperCase() === "POST",
      ),
    ).toBe(true);
  });

  it("shows read-only own servers for a non-self-service user (no delete)", async () => {
    stubServersView(
      {
        is_admin: false,
        owners: [
          {
            user_id: 7,
            username: "morris@example.com",
            derived_user_id: "morris",
            servers: [server()],
          },
        ],
      },
      STATS_OK,
      { can_create: false, reason: "", allow_resource_edit: false },
    );
    render(
      <ServersView
        currentUser={makeUser({
          id: 7,
          role: "user",
          self_service: false,
          user_id: "morris",
        })}
        isAdmin={false}
      />,
    );
    await screen.findByText(/morris@example.com/);
    // No resource-edit (not allowed) and no delete (not self-service).
    expect(
      screen.queryByRole("button", { name: /^Change resources$/ }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete$/ })).toBeNull();
  });

  it("keeps another user's servers read-only for a non-admin", async () => {
    stubServersView(
      {
        is_admin: false,
        owners: [
          {
            user_id: 8,
            username: "nadia@example.com",
            derived_user_id: "nadia",
            servers: [server({ id: 2, user_id: 8 })],
          },
        ],
      },
      STATS_OK,
      { can_create: false, reason: "", allow_resource_edit: false },
    );
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    await screen.findByText(/nadia@example.com/);
    expect(
      screen.queryByRole("button", { name: /^Change resources$/ }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /^Reboot$/ })).toBeNull();
  });

  it("lets an admin act on any owner's servers", async () => {
    stubServersView(
      {
        is_admin: true,
        owners: [
          {
            user_id: 8,
            username: "nadia@example.com",
            derived_user_id: "nadia",
            servers: [server({ id: 2, user_id: 8 })],
          },
        ],
      },
      STATS_OK,
      { can_create: true, reason: "", allow_resource_edit: true },
    );
    render(<ServersView currentUser={ADMIN} isAdmin />);
    expect(
      await screen.findByRole("button", { name: /^Change resources$/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Reboot$/ })).toBeInTheDocument();
  });

  it("renders the server info, usage charts and actions in one card (issue_023)", async () => {
    stubServersView(
      {
        is_admin: false,
        owners: [
          {
            user_id: 7,
            username: "morris@example.com",
            derived_user_id: "morris",
            servers: [server()],
          },
        ],
      },
      STATS_OK,
      { can_create: false, reason: "", allow_resource_edit: true },
    );
    render(<ServersView currentUser={SELF} isAdmin={false} />);
    await screen.findByText(/morris@example.com/);
    // A single .server-card holds the name/specs, the inline charts row, and
    // the action buttons together (no separate wrapper card, charts inline).
    const card = document.querySelector(".server-card");
    expect(card).not.toBeNull();
    const scoped = within(card as HTMLElement);
    expect(scoped.getByText(/2 CPU · 4 GB RAM · 20 GB disk/)).toBeInTheDocument();
    expect(scoped.getAllByRole("group", { name: "CPU" }).length).toBe(1);
    expect(
      scoped.getByRole("button", { name: /^Change resources$/ }),
    ).toBeInTheDocument();
    // The charts sit in the inline top row, not a separate wrapper.
    expect(card!.querySelector(".server-card-top .stats-cards")).not.toBeNull();
  });

  it("shows the admin's own servers first, then other users' (issue_024)", async () => {
    stubServersView(
      {
        is_admin: true,
        owners: [
          {
            user_id: 8,
            username: "nadia@example.com",
            derived_user_id: "nadia",
            servers: [server({ id: 2, user_id: 8, name: "nadia-box" })],
          },
          {
            // The signed-in admin is id 99.
            user_id: 99,
            username: "admin@example.com",
            derived_user_id: "admin",
            servers: [server({ id: 3, user_id: 99, name: "admin-box" })],
          },
        ],
      },
      STATS_OK,
      { can_create: true, reason: "", allow_resource_edit: true },
    );
    render(<ServersView currentUser={ADMIN} isAdmin />);
    await screen.findByRole("heading", { name: /^My servers$/ });
    const heads = screen.getAllByRole("heading");
    const texts = heads.map((h) => h.textContent);
    const myIdx = texts.findIndex((t) => t === "My servers");
    const usersIdx = texts.findIndex((t) => t === "Users' servers");
    expect(myIdx).toBeGreaterThanOrEqual(0);
    expect(usersIdx).toBeGreaterThan(myIdx);
    // The admin's own group (admin@example.com) renders before nadia's.
    const adminOwnerIdx = texts.findIndex((t) => t?.includes("admin@example.com"));
    const nadiaOwnerIdx = texts.findIndex((t) => t?.includes("nadia@example.com"));
    expect(adminOwnerIdx).toBeGreaterThan(myIdx);
    expect(nadiaOwnerIdx).toBeGreaterThan(usersIdx);
  });

  it("filters servers by the search box and hides non-matching owner groups (issue_024)", async () => {
    stubServersView(
      {
        is_admin: true,
        owners: [
          {
            user_id: 99,
            username: "admin@example.com",
            derived_user_id: "admin",
            servers: [server({ id: 3, user_id: 99, name: "alpha-server" })],
          },
          {
            user_id: 8,
            username: "nadia@example.com",
            derived_user_id: "nadia",
            servers: [server({ id: 2, user_id: 8, name: "beta-server" })],
          },
        ],
      },
      STATS_OK,
      { can_create: false, reason: "", allow_resource_edit: true },
    );
    render(<ServersView currentUser={ADMIN} isAdmin />);
    await screen.findByText("alpha-server - 10.0.7.42");
    expect(screen.getByText("beta-server - 10.0.7.42")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/filter servers/i), "alpha");
    expect(screen.getByText("alpha-server - 10.0.7.42")).toBeInTheDocument();
    expect(screen.queryByText("beta-server - 10.0.7.42")).toBeNull();
    // nadia's group has no match and is hidden entirely.
    expect(screen.queryByText(/nadia@example.com/)).toBeNull();

    // A query that matches nothing shows an explicit no-match message.
    await userEvent.clear(screen.getByLabelText(/filter servers/i));
    await userEvent.type(screen.getByLabelText(/filter servers/i), "zzzznope");
    expect(await screen.findByText(/no servers match the filter/i)).toBeInTheDocument();
  });
});
