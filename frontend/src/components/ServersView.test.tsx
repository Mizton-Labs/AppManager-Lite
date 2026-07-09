import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ServersView } from "./ServersView";
import type { ServersOverview, UserServer } from "../types";

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

function stubServersView(overview: ServersOverview, stats: unknown = STATS_OK) {
  const fetchMock = vi.fn(async (input: string) => {
    const url = String(input);
    const json = (payload: unknown, status = 200) =>
      ({ ok: status < 400, status, json: async () => payload }) as Response;
    if (url.includes("/api/servers/overview")) return json(overview);
    if (/\/stats(\?|$)/.test(url)) return json(stats);
    return json({ detail: "unexpected" }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

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
    render(<ServersView />);

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
    render(<ServersView />);
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
    render(<ServersView />);
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
    render(<ServersView />);
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
      return json({ detail: "unexpected" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ServersView />);
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
    render(<ServersView />);
    expect(
      await screen.findByText(/no usage data for this timeframe yet/i),
    ).toBeInTheDocument();
  });
});
