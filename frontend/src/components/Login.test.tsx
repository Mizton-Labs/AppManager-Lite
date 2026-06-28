import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Login } from "./Login";
import { setCsrfToken } from "../api";

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
  window.history.pushState({}, "", "/");
});

describe("Login", () => {
  it("submits credentials and reports the authenticated session", async () => {
    const session = {
      authenticated: true,
      enable_auth: true,
      user: {
        id: 1,
        username: "admin",
        role: "admin",
        is_active: true,
        must_change_password: true,
        self_service: true,
        apps_server: "",
        apps_server_ip: "",
        teams: [],
      },
      csrf_token: "csrf-1",
      auth_method: "local",
      app_name: "",
      app_logo: "",
      collaborators: [],
      configured: false,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ enabled: false, local_login_enabled: true, providers: [] }),
      } as Response)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => session,
      } as Response);
    vi.stubGlobal("fetch", fetchMock);

    const onAuthenticated = vi.fn();
    render(<Login onAuthenticated={onAuthenticated} />);

    await userEvent.type(screen.getByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "supersecret1");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(onAuthenticated).toHaveBeenCalledWith(session);
  });

  it("shows the server error message when sign-in fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ enabled: false, local_login_enabled: true, providers: [] }),
      } as Response)
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ detail: "Invalid username or password" }),
      } as Response);
    vi.stubGlobal("fetch", fetchMock);

    render(<Login onAuthenticated={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("Username"), "admin");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid username or password",
    );
  });

  it("renders SSO provider links when enabled", async () => {
    window.history.pushState({}, "", "/?next=/grafana/");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        enabled: true,
        local_login_enabled: true,
        providers: [
          { protocol: "oidc", label: "Google", login_url: "auth/oidc/login" },
          { protocol: "saml", label: "SAML SSO", login_url: "auth/saml/login" },
        ],
      }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    render(<Login onAuthenticated={vi.fn()} />);

    expect(await screen.findByRole("link", { name: "Sign in with Google" }))
      .toHaveAttribute(
        "href",
        "http://localhost:3000/api/auth/oidc/login?next=%2Fgrafana%2F",
      );
    expect(screen.getByRole("link", { name: "Sign in with SAML SSO" }))
      .toHaveAttribute(
        "href",
        "http://localhost:3000/api/auth/saml/login?next=%2Fgrafana%2F",
      );
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
  });

  it("ignores unsafe next values in SSO links", async () => {
    window.history.pushState({}, "", "/?next=https://evil.example/path");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        enabled: true,
        local_login_enabled: true,
        providers: [{ protocol: "oidc", label: "SSO", login_url: "auth/oidc/login" }],
      }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    render(<Login onAuthenticated={vi.fn()} />);

    expect(await screen.findByRole("link", { name: "Sign in with SSO" }))
      .toHaveAttribute("href", "http://localhost:3000/api/auth/oidc/login");
  });
});
