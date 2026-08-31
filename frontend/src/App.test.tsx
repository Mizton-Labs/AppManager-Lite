import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { SessionState } from "./types";

const mocks = vi.hoisted(() => ({
  getSession: vi.fn(),
}));

vi.mock("./api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    getSession: mocks.getSession,
    logout: vi.fn(),
  },
  setCsrfToken: vi.fn(),
}));

vi.mock("./components/Login", () => ({
  Login: () => <div>login</div>,
}));

vi.mock("./components/ChangePasswordForm", () => ({
  ChangePasswordForm: () => <div>change password form</div>,
}));

vi.mock("./components/PortalShell", () => ({
  PortalShell: () => <div>portal shell</div>,
}));

function session(authMethod: SessionState["auth_method"]): SessionState {
  return {
    authenticated: true,
    enable_auth: true,
    csrf_token: "csrf",
    auth_method: authMethod,
    app_name: "",
    app_logo: "",
    collaborators: [],
    configured: true,
    user: {
      id: 1,
      username: "sso.user@example.com",
      user_id: "sso-user",
      role: "user",
      is_active: true,
      must_change_password: true,
      self_service: false,
      apps_server: "",
      apps_server_ip: "",
      teams: [],
    },
  };
}

describe("App forced password reset", () => {
  beforeEach(() => {
    mocks.getSession.mockReset();
  });

  it("requires reset for local sessions with a pending password change", async () => {
    mocks.getSession.mockResolvedValue(session("local"));

    render(<App />);

    expect(await screen.findByText("Update your password")).toBeInTheDocument();
    expect(screen.getByText("change password form")).toBeInTheDocument();
  });

  it("does not require reset for SSO sessions with a pending password change", async () => {
    mocks.getSession.mockResolvedValue(session("oidc"));

    render(<App />);

    expect(await screen.findByText("portal shell")).toBeInTheDocument();
    expect(screen.queryByText("Update your password")).not.toBeInTheDocument();
  });
});

describe("App resumes a pending alias after authenticated bootstrap (issue_local_033)", () => {
  let originalLocation: Location;
  let replaceSpy: ReturnType<typeof vi.fn>;
  let baseEl: HTMLBaseElement;

  beforeEach(() => {
    mocks.getSession.mockReset();
    originalLocation = window.location;
    // The backend injects a real `<base href>` (matching APP_BASE_PREFIX)
    // into the served HTML at runtime; simulate that here so
    // `document.baseURI` reflects the portal's root the same way it does in
    // production, rather than jsdom's fallback of "no <base> tag means
    // baseURI is just the current document URL" (which would make every
    // route look like the landing page).
    baseEl = document.createElement("base");
    baseEl.href = "/";
    document.head.appendChild(baseEl);
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    });
    window.history.pushState({}, "", "/");
    baseEl.remove();
  });

  /** Snapshot the current (already-navigated-to) location and swap in a
   * spy for `.replace` only, so the test's `history.pushState` call (made
   * before this) is what determines pathname/search. */
  function mockLocationReplace(): void {
    replaceSpy = vi.fn();
    const current = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...current, replace: replaceSpy },
    });
  }

  function noPasswordChangeSession(
    overrides: Partial<SessionState> = {},
  ): SessionState {
    const base = session("oidc");
    base.user!.must_change_password = false;
    return { ...base, ...overrides };
  }

  it("replaces the URL with the safe next destination when authenticated on the landing page", async () => {
    window.history.pushState({}, "", "/?next=/latmov/");
    mockLocationReplace();
    mocks.getSession.mockResolvedValue(noPasswordChangeSession());

    render(<App />);

    await waitFor(() => expect(replaceSpy).toHaveBeenCalledWith("/latmov/"));
    // Home/PortalShell must never render while the redirect is pending.
    expect(screen.queryByText("portal shell")).not.toBeInTheDocument();
  });

  it("does not redirect when there is no next parameter", async () => {
    window.history.pushState({}, "", "/");
    mockLocationReplace();
    mocks.getSession.mockResolvedValue(noPasswordChangeSession());

    render(<App />);

    expect(await screen.findByText("portal shell")).toBeInTheDocument();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it("does not redirect for an unauthenticated session", async () => {
    window.history.pushState({}, "", "/?next=/latmov/");
    mockLocationReplace();
    mocks.getSession.mockResolvedValue({
      ...noPasswordChangeSession(),
      authenticated: false,
    });

    render(<App />);

    expect(await screen.findByText("login")).toBeInTheDocument();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it("does not redirect when authentication is disabled", async () => {
    window.history.pushState({}, "", "/?next=/latmov/");
    mockLocationReplace();
    mocks.getSession.mockResolvedValue({
      ...noPasswordChangeSession(),
      enable_auth: false,
    });

    render(<App />);

    expect(await screen.findByText("portal shell")).toBeInTheDocument();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it("does not redirect a local session with a pending mandatory password change", async () => {
    window.history.pushState({}, "", "/?next=/latmov/");
    mockLocationReplace();
    mocks.getSession.mockResolvedValue(session("local"));

    render(<App />);

    expect(await screen.findByText("Update your password")).toBeInTheDocument();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it("still redirects an SSO session despite an unused local must_change_password flag", async () => {
    window.history.pushState({}, "", "/?next=/latmov/");
    mockLocationReplace();
    mocks.getSession.mockResolvedValue(session("oidc")); // must_change_password: true

    render(<App />);

    await waitFor(() => expect(replaceSpy).toHaveBeenCalledWith("/latmov/"));
  });

  it("does not redirect when next is unsafe", async () => {
    window.history.pushState({}, "", "/?next=https://evil.example/path");
    mockLocationReplace();
    mocks.getSession.mockResolvedValue(noPasswordChangeSession());

    render(<App />);

    expect(await screen.findByText("portal shell")).toBeInTheDocument();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it("does not redirect when a stray next parameter appears on a non-landing route", async () => {
    window.history.pushState({}, "", "/some-other-route?next=/latmov/");
    mockLocationReplace();
    mocks.getSession.mockResolvedValue(noPasswordChangeSession());

    render(<App />);

    expect(await screen.findByText("portal shell")).toBeInTheDocument();
    expect(replaceSpy).not.toHaveBeenCalled();
  });
});
