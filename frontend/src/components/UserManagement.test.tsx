import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
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
      return json([
        makeUser({ id: 1, username: "admin", role: "admin" }),
        makeUser({ id: 2, username: "analyst", role: "user" }),
      ]);
    }
    if (method === "GET" && url.endsWith("/api/teams")) {
      return json([{ id: 1, name: "Red Team", sort_order: 0, icon: "" }]);
    }
    if (method === "GET" && url.endsWith("/api/settings/bundle-templates")) {
      return json([]);
    }
    if (method === "POST" && url.endsWith("/api/settings/bundle-templates")) {
      const body = JSON.parse(init?.body as string);
      return json({ id: 1, ...body });
    }
    if (method === "POST" && url.endsWith("/api/users")) {
      return json({
        user: makeUser({ id: 2, username: "newbie@example.com", role: "user" }),
        password: "Generated-Pass-123",
      });
    }
    if (method === "DELETE" && url.includes("/api/users/")) {
      return json({ detail: "User deleted" });
    }
    return { ok: false, status: 500, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function createUserAndOpenBanner() {
  await screen.findByRole("heading", { name: /create user/i });
  await userEvent.type(screen.getByLabelText(/username/i), "newbie@example.com");
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

    render(<UserManagement currentUser={makeUser({ id: 99, role: "admin" })} />);
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

  it("sends the delete-apps choice when deleting a user", async () => {
    const fetchMock = stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);

    const analystCard = (await screen.findByText("analyst")).closest("article");
    expect(analystCard).not.toBeNull();
    await userEvent.click(
      within(analystCard!).getByRole("button", { name: /^edit$/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await userEvent.click(screen.getByLabelText(/also delete this user's apps/i));
    await userEvent.click(screen.getByRole("button", { name: /confirm delete/i }));

    expect(
      fetchMock.mock.calls.some(([url, init]) =>
        String(url).includes("delete_apps=true") &&
        (init?.method ?? "GET") === "DELETE",
      ),
    ).toBe(true);
  });

  it("creates a bundle template", async () => {
    const fetchMock = stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);

    await screen.findByRole("heading", { name: /bundle templates/i });
    await userEvent.type(screen.getByLabelText(/template name/i), "Shell profile");
    await userEvent.type(screen.getByLabelText(/template content/i), "USER");
    await userEvent.type(screen.getByLabelText(/template field/i), "USER");
    await userEvent.click(screen.getByRole("button", { name: /add template/i }));

    const createCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/api/settings/bundle-templates") &&
        (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse((createCall![1] as RequestInit).body as string)).toMatchObject({
      name: "Shell profile",
      content: "USER",
      mappings: [{ field_name: "USER", source: "username" }],
    });
  });
});
