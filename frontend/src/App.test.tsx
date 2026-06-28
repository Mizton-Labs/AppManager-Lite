import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
