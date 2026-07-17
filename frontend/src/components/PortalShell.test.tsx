import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { PortalShell } from "./PortalShell";
import type { ApiUser, SessionState } from "../types";
import { makeUser } from "../test/fixtures";
import { ThemeProvider } from "../theme";

function makeSession(
  user: Partial<ApiUser> & Pick<ApiUser, "role">,
  overrides: Partial<SessionState> = {},
): SessionState {
  return {
    authenticated: true,
    enable_auth: true,
    csrf_token: "csrf",
    auth_method: "local",
    app_name: "",
    app_logo: "",
    collaborators: [],
    configured: true,
    user: makeUser({ username: "tester", ...user }),
    ...overrides,
  };
}

function renderShell(session: SessionState, initialPath = "/") {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <PortalShell
          session={session}
          onLogout={() => undefined}
          onPasswordChanged={() => undefined}
          onSessionRefresh={() => undefined}
        />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  // HomeView fetches the application list on mount; the sidebar fetches the
  // team list (/api/teams); Settings (first-run) fetches branding +
  // reverse-proxy settings. Return shapes that match each endpoint so these
  // structural tests do not hit the network.
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const url = String(input);
      const isSettings = /\/api\/settings\//.test(url);
      const isSshKeys = /\/api\/settings\/ssh-keys\b/.test(url);
      const isTeams = /\/api\/teams\b/.test(url);
      return {
        ok: true,
        status: 200,
        json: async () =>
          isSshKeys
            ? []
            : isTeams
            ? [
                { id: 1, name: "Threat Hunting", sort_order: 0, icon: "" },
                { id: 2, name: "Red Team", sort_order: 1, icon: "" },
              ]
            : isSettings
              ? {
                  app_name: "",
                  app_logo: "",
                  collaborators: [],
                  configured: false,
                  nginx_host: "",
                  nginx_user: "",
                  nginx_conf_path: "",
                  ssh_key_path: "",
                  alias_template: "",
                }
              : [],
      } as Response;
    }),
  );
});
afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("PortalShell", () => {
  it("shows every team and Settings for admins, with no top-bar Manage link", async () => {
    renderShell(makeSession({ role: "admin" }));

    // The top-bar Manage button was removed; management lives in the sidebar.
    expect(screen.queryByRole("link", { name: "Manage" })).toBeNull();

    const nav = screen.getByRole("navigation", { name: "Primary" });
    // issue_019: the theme selector was removed from the top bar.
    expect(screen.queryByLabelText("Theme")).toBeNull();
    expect(within(nav).getByRole("link", { name: "Home" })).toBeInTheDocument();
    // Team links are fetched from /api/teams and appear asynchronously.
    expect(
      await within(nav).findByRole("link", { name: "Threat Hunting" }),
    ).toBeInTheDocument();
    expect(
      within(nav).getByRole("link", { name: "Red Team" }),
    ).toBeInTheDocument();
    // Admins get App Manager and the admin-only Settings link.
    expect(
      within(nav).getByRole("link", { name: "App Manager" }),
    ).toBeInTheDocument();
    expect(
      within(nav).getByRole("link", { name: "Settings" }),
    ).toBeInTheDocument();
    expect(
      within(nav).getByRole("link", { name: "About" }),
    ).toBeInTheDocument();
    // Admins get the Audit log link.
    expect(
      within(nav).getByRole("link", { name: "Audit log" }),
    ).toBeInTheDocument();

    // Let the HomeView application fetch settle inside act().
    await screen.findByText(/No shared applications are available/i);
  });

  it("shows App Manager (not Settings) and every team to regular users", async () => {
    renderShell(makeSession({ role: "user", teams: ["Red Team"] }));

    expect(screen.queryByRole("link", { name: "Manage" })).toBeNull();

    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(
      await within(nav).findByRole("link", { name: "Red Team" }),
    ).toBeInTheDocument();
    expect(
      within(nav).getByRole("link", { name: "Threat Hunting" }),
    ).toBeInTheDocument();
    // App Manager is available to every signed-in user...
    expect(
      within(nav).getByRole("link", { name: "App Manager" }),
    ).toBeInTheDocument();
    // ...while Settings is admin-only.
    expect(
      within(nav).queryByRole("link", { name: "Settings" }),
    ).toBeNull();
    // Account is always available.
    expect(
      within(nav).getByRole("link", { name: "Account" }),
    ).toBeInTheDocument();
    // The Audit log link is admin-only.
    expect(
      within(nav).queryByRole("link", { name: "Audit log" }),
    ).toBeNull();

    // Let the HomeView application fetch settle inside act().
    await screen.findByText(/No shared applications are available/i);
  });

  it("redirects a non-admin away from the admin-only Settings route", async () => {
    renderShell(makeSession({ role: "user", teams: ["Red Team"] }), "/settings");

    // The route guard sends non-admins to Home; the Settings heading never
    // renders. (Backend endpoints are independently admin-gated.)
    await screen.findByText(/No shared applications are available/i);
    expect(
      screen.queryByRole("heading", { name: /^Settings$/ }),
    ).toBeNull();
  });

  it("toggles the sidebar collapsed state", async () => {
    renderShell(makeSession({ role: "admin" }));

    const collapse = screen.getByRole("button", { name: /collapse sidebar/i });
    expect(collapse).toHaveAttribute("aria-expanded", "true");

    await userEvent.click(collapse);

    const expand = screen.getByRole("button", { name: /expand sidebar/i });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(localStorage.getItem("appmanager-lite.sidebar.collapsed")).toBe("1");
  });

  it("runs the content section full-bleed only on the embedded route", async () => {
    // A normal route uses the default centered/padded content section.
    const { container, unmount } = renderShell(makeSession({ role: "admin" }));
    const main = container.querySelector("main.shell-main");
    expect(main).not.toBeNull();
    expect(main?.classList.contains("full-bleed")).toBe(false);
    unmount();

    // The embedded route drops the width cap/padding so the frame fills the
    // whole content section (and grows when the sidebar collapses).
    const { container: embContainer } = renderShell(
      makeSession({ role: "admin" }),
      "/embedded/1",
    );
    const embMain = embContainer.querySelector("main.shell-main");
    expect(embMain?.classList.contains("full-bleed")).toBe(true);
  });

  it("detects the embedded route under a deployment base prefix", async () => {
    // Under a mounted base path, React Router strips the basename from
    // useLocation().pathname, so the "/embedded/" match must still fire.
    const { container } = render(
      <ThemeProvider>
        <MemoryRouter basename="/home" initialEntries={["/home/embedded/1"]}>
          <PortalShell
            session={makeSession({ role: "admin" })}
            onLogout={() => undefined}
            onPasswordChanged={() => undefined}
            onSessionRefresh={() => undefined}
          />
        </MemoryRouter>
      </ThemeProvider>,
    );
    const main = container.querySelector("main.shell-main");
    expect(main?.classList.contains("full-bleed")).toBe(true);
  });

  it("redirects an admin to Settings on first run (not yet configured)", async () => {
    renderShell(makeSession({ role: "admin" }, { configured: false }));

    // The first-login wizard sends the admin straight to Settings with the
    // setup prompt visible, landing on the Reverse Proxy sub-tab (the form
    // whose save completes first-run setup).
    expect(
      await screen.findByText(/finish setup by setting branding/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /reverse proxy configuration/i }),
    ).toBeInTheDocument();
  });

  it("does not redirect once the deployment is configured", async () => {
    renderShell(makeSession({ role: "admin" }, { configured: true }));

    // Lands on Home; no setup prompt.
    await screen.findByText(/No shared applications are available/i);
    expect(
      screen.queryByText(/finish setup by setting your application name/i),
    ).not.toBeInTheDocument();
  });
});
