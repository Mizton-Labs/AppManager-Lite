import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserServersPanel } from "./UserServers";
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

function stubServers(initial: UserServer[] = []) {
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
});
