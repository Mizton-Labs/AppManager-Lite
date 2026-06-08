import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Login } from "./Login";
import { setCsrfToken } from "../api";

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
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
        teams: [],
      },
      csrf_token: "csrf-1",
    };
    const fetchMock = vi.fn().mockResolvedValue({
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
    const fetchMock = vi.fn().mockResolvedValue({
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
});
