import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApplicationManager } from "./ApplicationManager";
import type { Application } from "../types";
import { makeApp } from "../test/fixtures";

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
function stubBackend(initial: Application[]) {
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
        approval_status: "pending",
        sort_order: store.length,
      });
      store = [...store, created];
      return jsonResponse(created);
    }
    if (method === "PATCH" && byId) {
      const id = Number(byId[1]);
      store = store.map((app) => (app.id === id ? { ...app, ...body } : app));
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

afterEach(() => vi.unstubAllGlobals());

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
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/applications/manage",
    );
  });

  it("loads the personal endpoint for members", async () => {
    const fetchMock = stubBackend([makeApp({ approval_status: "pending" })]);
    render(<ApplicationManager isAdmin={false} teamOptions={["Threat Hunting"]} />);

    expect(await screen.findByText("Hunt Workbench")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/api/applications/mine",
    );
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

  it("lets an admin set the apps server and port on a new alias application", async () => {
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
    await userEvent.type(screen.getByLabelText("Application port"), "8080");
    // The admin-only apps server field is shown for alias apps.
    const serverField = screen.getByLabelText("Apps server host or IP");
    await userEvent.type(serverField, "apps.example.com");
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
        apps_port: "8080",
        apps_server: "apps.example.com",
      },
    );
  });

  it("hides the apps server field from non-admins and sends only the port", async () => {
    const fetchMock = stubBackend([]);
    render(
      <ApplicationManager isAdmin={false} teamOptions={["Threat Hunting"]} />,
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
    await userEvent.type(screen.getByLabelText("Application port"), "8080");
    // No apps-server field for non-admins.
    expect(screen.queryByLabelText("Apps server host or IP")).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    expect(await screen.findByText("Member Alias")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "POST",
    );
    const sent = JSON.parse((postCall![1] as RequestInit).body as string);
    expect(sent).toMatchObject({ name: "Member Alias", apps_port: "8080" });
    expect(sent).not.toHaveProperty("apps_server");
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
    // Alias is the default mode; the port input is shown (no server input).
    await userEvent.type(
      screen.getByLabelText(/local alias relative path/i),
      "grafana",
    );
    await userEvent.type(screen.getByLabelText(/application port/i), "8080");
    await userEvent.click(
      screen.getByRole("button", { name: /create application/i }),
    );

    expect(await screen.findByText("Alias App")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse((postCall![1] as RequestInit).body as string)).toMatchObject({
      url_type: "alias",
      apps_port: "8080",
    });
    // Admins have an apps-server field; left blank it sends an empty server.
    expect(
      JSON.parse((postCall![1] as RequestInit).body as string).apps_server,
    ).toBe("");
  });

  it("shows a port field (and no server field) for non-admins on an alias", async () => {
    stubBackend([]);
    render(<ApplicationManager isAdmin={false} teamOptions={["Threat Hunting"]} />);

    await screen.findByText(/have not submitted any applications/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );
    // Alias mode is default: any user sets the port; no server input exists.
    expect(screen.getByLabelText(/application port/i)).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Apps server host or IP"),
    ).not.toBeInTheDocument();
  });

  it("hides the port field for a full URL", async () => {
    stubBackend([]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    await screen.findByText(/No applications yet/i);
    await userEvent.click(
      screen.getByRole("button", { name: /new application/i }),
    );
    expect(screen.getByLabelText(/application port/i)).toBeInTheDocument();
    // Switching to Full URL hides the port field.
    await userEvent.click(screen.getByRole("radio", { name: /full url/i }));
    expect(screen.queryByLabelText(/application port/i)).not.toBeInTheDocument();
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
    // accepts only letters, digits, and dashes (separators are stripped).
    await userEvent.type(
      screen.getByLabelText(/local alias relative path/i),
      "wiki-home",
    );
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
        url: "wiki-home",
        url_type: "alias",
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
        needs_push: true,
        pending_is_active: false,
      }),
    ]);
    render(<ApplicationManager isAdmin teamOptions={ALL_TEAMS} />);

    expect(await screen.findByText(/published by analyst/i)).toBeInTheDocument();
    expect(screen.getByText(/disable requested/i)).toBeInTheDocument();
    expect(screen.getByText(/proxy config changed/i)).toBeInTheDocument();
  });
});
