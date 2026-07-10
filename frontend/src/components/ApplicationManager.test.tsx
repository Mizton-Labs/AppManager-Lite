import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApplicationManager } from "./ApplicationManager";
import type { Application, UserServer } from "../types";
import { makeApp, makeUser } from "../test/fixtures";

/** Build a {@link UserServer} for tests; override only what a case cares about. */
function makeUserServer(overrides: Partial<UserServer> = {}): UserServer {
  return {
    id: 1,
    user_id: 1,
    name: "apps-lxc",
    hostname: "apps-lxc.internal",
    template_id: 1,
    template_name: "apps-lxc",
    vmid: 500,
    node: "pve1",
    kind: "lxc",
    ip_address: "",
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
    created_at: "",
    is_apps_server: true,
    ...overrides,
  };
}

// Team options the Application Manager renders in its picker. Previously this
// mirrored the hardcoded ALL_TEAMS; teams are now admin-managed, so the test
// supplies its own representative list.
const ALL_TEAMS = [
  "Detect and Response",
  "Threat Hunting",
  "Threat Intel",
  "Forensics & BID",
  "Advanced Analytics",
  "Red Team",
  "Threat Detection Engineering",
] as const;

function jsonResponse(payload: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => payload } as Response;
}

/**
 * Install a small stateful fetch mock that emulates the applications API so the
 * component's reload-after-mutation flow behaves like the real backend. Both the
 * admin (`/manage`) and member (`/mine`) listing endpoints return the store.
 */
function stubBackend(
  initial: Application[],
  serversByOwner: Record<number, UserServer[]> = {},
) {
  let store = [...initial];
  let nextId = store.reduce((max, app) => Math.max(max, app.id), 0) + 1;

  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    const byId = url.match(/\/api\/applications\/(\d+)$/);

    if (method === "GET" && /\/api\/applications\/(manage|mine)$/.test(url)) {
      return jsonResponse(store);
    }
    const ownerServersMatch = url.match(/\/api\/users\/(\d+)\/servers$/);
    if (method === "GET" && ownerServersMatch) {
      return jsonResponse(serversByOwner[Number(ownerServersMatch[1])] ?? []);
    }
    if (method === "GET" && url.endsWith("/api/users")) {
      return jsonResponse([
        {
          id: 1,
          username: "admin@example.com",
          role: "admin",
          is_active: true,
          must_change_password: false,
          self_service: true,
          apps_server: "",
          apps_server_ip: "",
          teams: [],
        },
        {
          id: 2,
          username: "owner@example.com",
          role: "user",
          is_active: true,
          must_change_password: false,
          self_service: false,
          apps_server: "",
          apps_server_ip: "",
          teams: ["Red Team"],
        },
      ]);
    }
    const aliasConfigMatch = url.match(/\/api\/applications\/(\d+)\/alias-config$/);
    if (method === "GET" && aliasConfigMatch) {
      const app = store.find((item) => item.id === Number(aliasConfigMatch[1]));
      return jsonResponse({
        status: app?.url_type === "alias" ? "ok" : "skipped",
        log: app?.url_type === "alias" ? "Loaded current deployed config from nginx." : "Skipped.",
        alias: app?.url ?? "",
        apps_protocol: app?.apps_protocol ?? "http",
        apps_server: app?.apps_server ?? "",
        apps_port: app?.apps_port ?? "",
        apps_path: app?.apps_path ?? "",
        alias_auth_required: app?.alias_auth_required ?? true,
      });
    }
    const retryMatch = url.match(/\/api\/applications\/(\d+)\/push-retry$/);
    if (method === "POST" && retryMatch) {
      const id = Number(retryMatch[1]);
      // Simulate a successful retry: status flips to "ok".
      store = store.map((app) =>
        app.id === id
          ? { ...app, last_push_status: "ok", last_push_log: "[OK] nginx reloaded" }
          : app,
      );
      return jsonResponse(store.find((app) => app.id === id));
    }
    if (method === "POST" && url.endsWith("/api/applications")) {
      const created = makeApp({
        id: nextId++,
        name: body.name,
        url: body.url,
        url_type: body.url_type ?? "url",
        description: body.description ?? "",
        icon_url: body.icon_url ?? "",
        teams: body.teams ?? [],
        apps_server: body.apps_server ?? "",
        apps_protocol: body.apps_protocol ?? "http",
        apps_port: body.apps_port ?? "",
        apps_path: body.apps_path ?? "",
        alias_auth_required: body.alias_auth_required ?? true,
        approval_status: "pending",
        sort_order: store.length,
      });
      store = [...store, created];
      return jsonResponse(created);
    }
    if (method === "PATCH" && byId) {
      const id = Number(byId[1]);
      const selectedOwner = body.created_by
        ? body.created_by === 1
          ? "admin@example.com"
          : "owner@example.com"
        : undefined;
      store = store.map((app) =>
        app.id === id
          ? {
              ...app,
              ...body,
              ...(selectedOwner
                ? { created_by_id: body.created_by, created_by: selectedOwner }
                : {}),
            }
          : app,
      );
      return jsonResponse(store.find((app) => app.id === id));
    }
    if (method === "DELETE" && byId) {
      const id = Number(byId[1]);
      store = store.filter((app) => app.id !== id);
      return jsonResponse({ detail: "Application deleted." });
    }
    return jsonResponse({ detail: "unexpected request" }, false, 500);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

describe("ApplicationManager", () => {
  it("loads the management endpoint for administrators and flags disabled apps", async () => {
    const fetchMock = stubBackend([
      makeApp(),
      makeApp({ id: 2, name: "Retired Tool", is_active: false }),
    ]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    expect(await screen.findByText("Hunt Workbench")).toBeInTheDocument();
    expect(screen.getByText("Retired Tool")).toBeInTheDocument();
    expect(screen.getByText("disabled")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some((c) =>
        String(c[0]).includes("/api/applications/manage"),
      ),
    ).toBe(true);
  });

  it("loads the personal endpoint for members", async () => {
    const fetchMock = stubBackend([makeApp({ approval_status: "pending" })]);
    render(<ApplicationManager isAdmin={false} teamOptions={["Threat Hunting"]} />);

    expect(await screen.findByText("Hunt Workbench")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some((c) =>
        String(c[0]).includes("/api/applications/mine"),
      ),
    ).toBe(true);
  });

  it("creates an application behind the New application button and always sends url_type", async () => {
    const fetchMock = stubBackend([]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText(/No applications yet/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );

    await userEvent.type(screen.getByLabelText("Name"), "New Tool");
    // Switch from the default alias mode to a full URL.
    await userEvent.click(screen.getByRole("radio", { name: /full url/i }));
    await userEvent.type(
      screen.getByLabelText("Full URL address"),
      "https://example.com/new",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    expect(await screen.findByText("New Tool")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse((postCall![1] as RequestInit).body as string)).toMatchObject(
      {
        name: "New Tool",
        url: "https://example.com/new",
        url_type: "url",
        teams: [],
      },
    );
  });

  it("offers an owner's apps-server servers as a dropdown (by name) with a Custom fallback", async () => {
    const fetchMock = stubBackend([], {
      1: [
        makeUserServer({
          id: 101, user_id: 1, name: "apps-lxc",
          hostname: "apps-lxc.internal", is_apps_server: true,
        }),
        makeUserServer({
          id: 102, user_id: 1, name: "plain-server",
          hostname: "plain.internal", is_apps_server: false,
        }),
      ],
    });
    render(
      <ApplicationManager
        isAdmin
        teamOptions={ALL_TEAMS}
        currentUser={makeUser({ id: 1, role: "admin", username: "admin@example.com" })}
      />,
    );

    await screen.findByText(/No applications yet/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );
    await userEvent.type(screen.getByLabelText("Name"), "Aliased");

    await userEvent.type(
      screen.getByLabelText("Local alias relative path"),
      "aliased",
    );
    const select = await screen.findByLabelText(/alias upstream apps server/i);
    // The dropdown offers the server by name; the non-apps-server "plain"
    // server is excluded.
    expect(
      screen.getByRole("option", { name: "apps-lxc" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "plain-server" }),
    ).toBeNull();
    await userEvent.selectOptions(select, "apps-lxc.internal");
    await userEvent.type(screen.getByLabelText(/alias upstream port/i), "8080");
    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    const postCall = fetchMock.mock.calls.find(
      ([u, init]) =>
        String(u).endsWith("/api/applications") &&
        (init?.method ?? "GET") === "POST",
    );
    // The value stored/pushed is the resolvable host, not the server's name.
    expect(
      JSON.parse((postCall![1] as RequestInit).body as string),
    ).toMatchObject({ apps_server: "apps-lxc.internal" });
  });

  it("defaults new applications to the local-alias radio", async () => {
    stubBackend([]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText(/No applications yet/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );

    expect(screen.getByRole("radio", { name: /local alias/i })).toBeChecked();
    expect(screen.getByRole("radio", { name: /full url/i })).not.toBeChecked();
  });

  it("lets an admin set alias upstream settings on a new alias application", async () => {
    const fetchMock = stubBackend([]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText(/No applications yet/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );

    await userEvent.type(screen.getByLabelText("Name"), "Admin Alias");
    await userEvent.type(
      screen.getByLabelText("Local alias relative path"),
      "adminalias",
    );
    await userEvent.click(screen.getByLabelText("Alias upstream protocol"));
    await userEvent.selectOptions(screen.getByLabelText("Alias upstream protocol"), "https");
    await userEvent.type(screen.getByLabelText("Alias upstream port"), "8080");
    const serverField = screen.getByLabelText("Alias upstream server host or IP");
    await userEvent.type(serverField, "apps.example.com");
    await userEvent.type(screen.getByLabelText("Alias upstream suffix path"), "dash");
    expect(screen.getByText(/https:\/\/apps\.example\.com:8080\/dash/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    expect(await screen.findByText("Admin Alias")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse((postCall![1] as RequestInit).body as string)).toMatchObject(
      {
        name: "Admin Alias",
        url_type: "alias",
        apps_protocol: "https",
        apps_port: "8080",
        apps_server: "apps.example.com",
        apps_path: "/dash",
      },
    );
  });

  it("prefills and sends alias upstream host for non-admins", async () => {
    const fetchMock = stubBackend([]);
    render(
      <ApplicationManager
        isAdmin={false}
        teamOptions={["Threat Hunting"]}
        currentUser={{
          id: 2,
          username: "member@example.com",
          user_id: "member",
          role: "user",
          is_active: true,
          must_change_password: false,
          self_service: false,
          apps_server: "member-apps.example.com",
          apps_server_ip: "10.0.0.8",
          teams: ["Threat Hunting"],
        }}
      />,
    );

    await screen.findByText(/have not submitted any applications/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );

    await userEvent.type(screen.getByLabelText("Name"), "Member Alias");
    await userEvent.type(
      screen.getByLabelText("Local alias relative path"),
      "memberalias",
    );
    await userEvent.type(screen.getByLabelText("Alias upstream port"), "8080");
    expect(screen.getByLabelText("Alias upstream server host or IP")).toHaveValue(
      "member-apps.example.com",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    expect(await screen.findByText("Member Alias")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "POST",
    );
    const sent = JSON.parse((postCall![1] as RequestInit).body as string);
    expect(sent).toMatchObject({
      name: "Member Alias",
      apps_protocol: "http",
      apps_server: "member-apps.example.com",
      apps_port: "8080",
      apps_path: "",
    });
  });

  it("selects and clears all teams with the Select all toggle", async () => {
    stubBackend([]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText(/No applications yet/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );

    const teamBoxes = () =>
      screen
        .getByRole("group", { name: /teams/i })
        .querySelectorAll('input[type="checkbox"]');

    // Initially none selected.
    expect(
      [...teamBoxes()].every((b) => !(b as HTMLInputElement).checked),
    ).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: /select all/i }));
    expect(
      [...teamBoxes()].every((b) => (b as HTMLInputElement).checked),
    ).toBe(true);

    // The toggle now clears the selection.
    await userEvent.click(screen.getByRole("button", { name: /clear all/i }));
    expect(
      [...teamBoxes()].every((b) => !(b as HTMLInputElement).checked),
    ).toBe(true);
  });

  it("lets a user set the port on an alias app and sends it", async () => {
    const fetchMock = stubBackend([]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText(/No applications yet/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );

    await userEvent.type(screen.getByLabelText("Name"), "Alias App");
    // Alias is the default mode; upstream settings are shown.
    await userEvent.type(
      screen.getByLabelText(/local alias relative path/i),
      "grafana",
    );
    await userEvent.type(
      screen.getByLabelText(/alias upstream server host or ip/i),
      "apps.example.com",
    );
    await userEvent.type(screen.getByLabelText(/alias upstream port/i), "8080");
    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    expect(await screen.findByText("Alias App")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse((postCall![1] as RequestInit).body as string)).toMatchObject({
      url_type: "alias",
      apps_protocol: "http",
      apps_port: "8080",
      apps_path: "",
    });
    // The alias upstream server is sent from the structured alias fields.
    expect(
      JSON.parse((postCall![1] as RequestInit).body as string).apps_server,
    ).toBe("apps.example.com");
  });

  it("shows alias upstream fields for non-admins on an alias", async () => {
    stubBackend([]);
    render(<ApplicationManager isAdmin={false} teamOptions={["Threat Hunting"]} />);

    await screen.findByText(/have not submitted any applications/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );
    expect(screen.getByLabelText(/alias upstream protocol/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/alias upstream server host or ip/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/alias upstream port/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/alias upstream suffix path/i)).toBeInTheDocument();
  });

  it("hides alias upstream fields for a full URL", async () => {
    stubBackend([]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText(/No applications yet/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );
    expect(screen.getByLabelText(/alias upstream port/i)).toBeInTheDocument();
    // Switching to Full URL hides alias upstream settings.
    await userEvent.click(screen.getByRole("radio", { name: /full url/i }));
    expect(screen.queryByLabelText(/alias upstream port/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/full url address/i)).toBeInTheDocument();
  });

  it("submits a local alias with its url_type and without http validation", async () => {
    const fetchMock = stubBackend([]);
    render(<ApplicationManager isAdmin={false} teamOptions={["Threat Hunting"]} />);

    await screen.findByText(/have not submitted any applications/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );

    await userEvent.type(screen.getByLabelText("Name"), "Internal Wiki");
    // Alias is the default mode; type into the alias input directly. The input
    // accepts letters, digits, underscores, and dashes (separators are stripped).
    await userEvent.type(
      screen.getByLabelText(/local alias relative path/i),
      "wiki_home",
    );
    await userEvent.type(
      screen.getByLabelText(/alias upstream server host or ip/i),
      "apps.example.com",
    );
    await userEvent.type(screen.getByLabelText(/alias upstream port/i), "8080");
    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    expect(await screen.findByText("Internal Wiki")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse((postCall![1] as RequestInit).body as string)).toMatchObject(
      {
        name: "Internal Wiki",
        url: "wiki_home",
        url_type: "alias",
      },
    );
  });

  it("warns and submits when alias authentication is disabled", async () => {
    const fetchMock = stubBackend([]);
    render(<ApplicationManager isAdmin={false} teamOptions={["Threat Hunting"]} />);

    await screen.findByText(/have not submitted any applications/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );

    await userEvent.type(screen.getByLabelText("Name"), "Public Status");
    await userEvent.type(
      screen.getByLabelText(/local alias relative path/i),
      "status",
    );
    await userEvent.type(
      screen.getByLabelText(/alias upstream server host or ip/i),
      "apps.example.com",
    );
    await userEvent.type(screen.getByLabelText(/alias upstream port/i), "8080");
    await userEvent.click(
      screen.getByRole("checkbox", { name: /require appmanager authentication/i }),
    );

    expect(
      screen.getByText(/reachable without an AppManager session/i),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse((postCall![1] as RequestInit).body as string)).toMatchObject(
      {
        url: "status",
        url_type: "alias",
        alias_auth_required: false,
      },
    );
  });

  it("strips disallowed characters typed into the alias input", async () => {
    const fetchMock = stubBackend([]);
    render(<ApplicationManager isAdmin={false} teamOptions={["Threat Hunting"]} />);

    await screen.findByText(/have not submitted any applications/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );

    await userEvent.type(screen.getByLabelText("Name"), "Sliced");
    // A leading slash and inner separators are removed as the user types.
    await userEvent.type(
      screen.getByLabelText(/local alias relative path/i),
      "/tools/x",
    );
    await userEvent.type(
      screen.getByLabelText(/alias upstream server host or ip/i),
      "apps.example.com",
    );
    await userEvent.type(screen.getByLabelText(/alias upstream port/i), "8080");
    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    expect(await screen.findByText("Sliced")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse((postCall![1] as RequestInit).body as string)).toMatchObject(
      {
        url: "toolsx",
        url_type: "alias",
      },
    );
  });

  it("lets an administrator approve a pending application", async () => {
    const fetchMock = stubBackend([makeApp({ approval_status: "pending" })]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("Hunt Workbench");
    expect(screen.getByText("pending")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /approve/i }));

    expect(await screen.findByText("approved")).toBeInTheDocument();
    const patchCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "PATCH",
    );
    expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toEqual({
      approval_status: "approved",
    });
  });

  it("lets an administrator reject a pending application", async () => {
    const fetchMock = stubBackend([makeApp({ approval_status: "pending" })]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("Hunt Workbench");
    await userEvent.click(screen.getByRole("button", { name: /reject/i }));

    expect(await screen.findByText("rejected")).toBeInTheDocument();
    const patchCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "PATCH",
    );
    expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toEqual({
      approval_status: "rejected",
    });
  });

  it("hides the reject action once an application is approved", async () => {
    stubBackend([makeApp({ approval_status: "approved" })]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("Hunt Workbench");
    // Approved apps may only be disabled or deleted, not rejected.
    expect(
      screen.queryByRole("button", { name: /reject/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /approve/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /disable/i }),
    ).toBeInTheDocument();
  });

  it("disables an application via the row action", async () => {
    const fetchMock = stubBackend([makeApp()]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("Hunt Workbench");
    await userEvent.click(screen.getByRole("button", { name: /disable/i }));

    expect(await screen.findByText("disabled")).toBeInTheDocument();
    const patchCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "PATCH",
    );
    expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toEqual({
      is_active: false,
    });
  });

  it("deletes an application after confirmation", async () => {
    stubBackend([makeApp()]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("Hunt Workbench");
    await userEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await userEvent.click(
      screen.getByRole("button", { name: /confirm delete/i }),
    );

    expect(await screen.findByText(/No applications yet/i)).toBeInTheDocument();
  });

  it("shows the reverse-proxy push log for an admin when the card is expanded", async () => {
    stubBackend([
      makeApp({
        last_push_status: "ok",
        last_push_log: "[OK] SSH access\n[OK] nginx reloaded",
      }),
    ]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("Hunt Workbench");
    // Not visible until the card is expanded (Edit).
    expect(
      screen.queryByRole("button", { name: /view push log/i }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    const viewLog = screen.getByRole("button", { name: /view push log/i });
    await userEvent.click(viewLog);
    expect(screen.getByText(/nginx reloaded/i)).toBeInTheDocument();
  });

  it("does not show a push button for a full-url app", async () => {
    stubBackend([makeApp({ last_push_status: "ok", last_push_log: "ok" })]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("Hunt Workbench");
    await userEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    expect(
      screen.queryByRole("button", { name: /^push$/i }),
    ).not.toBeInTheDocument();
  });

  it("opens and preloads an alias app from the editApp query", async () => {
    window.history.pushState({}, "", "/settings?editApp=42");
    stubBackend([
      makeApp({
        id: 42,
        name: "Alias Edit",
        url_type: "alias",
        url: "alias-edit",
        apps_protocol: "https",
        apps_server: "deployed.example.com",
        apps_port: "9443",
        apps_path: "/deployed",
      }),
    ]);

    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    expect(await screen.findByDisplayValue("alias-edit")).toBeInTheDocument();
    expect(
      await screen.findByText(/Loaded current deployed config from nginx/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/alias upstream protocol/i)).toHaveValue("https");
    expect(screen.getByLabelText(/alias upstream server host or ip/i)).toHaveValue(
      "deployed.example.com",
    );
    expect(screen.getByLabelText(/alias upstream port/i)).toHaveValue("9443");
    expect(screen.getByLabelText(/alias upstream suffix path/i)).toHaveValue("/deployed");
  });

  it("pushes an approved alias app and updates the status", async () => {
    const fetchMock = stubBackend([
      makeApp({
        url: "hunt",
        url_type: "alias",
        last_push_status: "failed",
        last_push_log: "[FAIL] reload",
      }),
    ]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("Hunt Workbench");
    await userEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    // The failed indicator and the Push button are shown.
    expect(screen.getByText(/proxy: failed/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^push$/i }));

    // The retry endpoint was called and the status flips to ok. The status now
    // appears both as the transient notice (next to the name) and in the
    // expanded push-log block.
    expect(
      fetchMock.mock.calls.some(([u, init]) =>
        String(u).endsWith("/push-retry") &&
        (init?.method ?? "GET") === "POST",
      ),
    ).toBe(true);
    expect((await screen.findAllByText(/proxy: ok/i)).length).toBeGreaterThan(0);
  });

  it("shows publisher and push-needed notices to admins", async () => {
    stubBackend([
      makeApp({
        created_by: "analyst@example.com",
        publisher_team: "Red Team",
        needs_push: true,
        pending_is_active: false,
      }),
    ]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    expect(await screen.findByText(/published by: analyst/i)).toBeInTheDocument();
    expect(screen.getByText("Team: Red Team")).toBeInTheDocument();
    expect(screen.getByText(/disable requested/i)).toBeInTheDocument();
    expect(screen.getByText(/proxy config changed/i)).toBeInTheDocument();
  });

  it("lets an admin transfer application ownership", async () => {
    const fetchMock = stubBackend([
      makeApp({ created_by: "admin@example.com", created_by_id: 1 }),
    ]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("Hunt Workbench");
    await userEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    await userEvent.selectOptions(screen.getByLabelText("Owner"), "2");
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    const patchCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).includes("/api/applications/1") &&
        (init?.method ?? "GET") === "PATCH",
    );
    expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toMatchObject({
      created_by: 2,
    });
  });

  it("reorders applications with move buttons and persists sort_order", async () => {
    const fetchMock = stubBackend([
      makeApp({ id: 1, name: "First App", sort_order: 0 }),
      makeApp({ id: 2, name: "Second App", sort_order: 1 }),
    ]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText("First App");
    await userEvent.click(screen.getByRole("button", { name: /move second app up/i }));

    const orderUpdates = fetchMock.mock.calls
      .filter(
        ([url, init]) =>
          String(url).includes("/api/applications/") &&
          (init?.method ?? "GET") === "PATCH" &&
          JSON.parse((init?.body ?? "{}") as string).sort_order !== undefined,
      )
      .map(([url, init]) => ({
        url: String(url),
        body: JSON.parse((init!.body ?? "{}") as string),
      }));
    expect(orderUpdates).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ url: expect.stringContaining("/api/applications/2"), body: { sort_order: 0 } }),
        expect.objectContaining({ url: expect.stringContaining("/api/applications/1"), body: { sort_order: 1 } }),
      ]),
    );
  });
});
