import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { UserManagement } from "./UserManagement";
import { makeUser } from "../test/fixtures";

/**
 * Stub the user-management endpoints. POST /api/users returns a generated
 * password so the credential banner (with its Copy button) is shown.
 */
function stubUsers() {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const json = (payload: unknown) =>
      ({ ok: true, status: 200, json: async () => payload }) as Response;

    if (method === "GET" && url.endsWith("/api/users")) {
      return json([makeUser({ id: 1, username: "admin", role: "admin" })]);
    }
    if (method === "GET" && url.endsWith("/api/teams")) {
      return json(["Red Team"]);
    }
    if (method === "POST" && url.endsWith("/api/users")) {
      return json({
        user: makeUser({ id: 2, username: "newbie", role: "user" }),
        password: "Generated-Pass-123",
      });
    }
    return { ok: false, status: 500, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function createUserAndOpenBanner() {
  await screen.findByRole("heading", { name: /create user/i });
  await userEvent.type(screen.getByLabelText(/username/i), "newbie");
  await userEvent.click(screen.getByRole("button", { name: /^create user$/i }));
  // Banner shows the generated password.
  await screen.findByText("Generated-Pass-123");
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (document as unknown as { execCommand?: unknown }).execCommand;
});

describe("UserManagement credential copy", () => {
  it("copies the generated password via the Clipboard API", async () => {
    stubUsers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    await createUserAndOpenBanner();

    await userEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    expect(writeText).toHaveBeenCalledWith("Generated-Pass-123");
    expect(await screen.findByRole("button", { name: /copied/i })).toBeInTheDocument();
  });

  it("shows a manual-copy hint when the clipboard is unavailable", async () => {
    stubUsers();
    // Insecure context: no Clipboard API, and execCommand reports failure.
    vi.stubGlobal("navigator", {});
    (document as unknown as { execCommand: unknown }).execCommand = vi
      .fn()
      .mockReturnValue(false);

    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    await createUserAndOpenBanner();

    await userEvent.click(screen.getByRole("button", { name: /^copy$/i }));
    expect(
      await screen.findByText(/select it, then\s+copy manually/i),
    ).toBeInTheDocument();
  });
});
