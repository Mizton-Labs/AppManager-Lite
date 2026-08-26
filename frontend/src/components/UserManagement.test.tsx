import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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
        makeUser({ id: 1, username: "admin", role: "admin", user_id: "admin" }),
        makeUser({ id: 2, username: "analyst", role: "user", user_id: "analyst" }),
        makeUser({
          id: 3,
          username: "analyst.one@example.com",
          role: "user",
          user_id: "analyst-one",
        }),
      ]);
    }
    if (method === "GET" && url.endsWith("/api/teams")) {
      return json([{ id: 1, name: "Red Team", sort_order: 0, icon: "" }]);
    }
    if (method === "GET" && url.endsWith("/api/settings/bundle-templates")) {
      return json([]);
    }
    if (method === "GET" && /\/api\/users\/\d+\/servers$/.test(url)) {
      return json([]);
    }
    if (method === "GET" && url.endsWith("/api/account/server-templates")) {
      return json([]);
    }
    if (method === "GET" && url.endsWith("/api/settings/server-templates")) {
      return json([
        { id: 7, vmid: 9001, name: "Debian Coder", kind: "lxc",
          admin_ssh_key_path: "", admin_ssh_key_id: null,
          main_os_user: "coder", enable_sudo: true,
          enable_trusted_access: true, is_apps_server: false },
      ]);
    }
    if (method === "POST" && url.endsWith("/api/settings/bundle-templates")) {
      const body = JSON.parse(init?.body as string);
      return json({ id: 1, ...body });
    }
    if (method === "POST" && url.endsWith("/api/users")) {
      return json({
        user: makeUser({ id: 2, username: "newbie@example.com", role: "user" }),
        password: "Generated-Pass-123",
        provisioning: [
          { template_id: 7, template_name: "Debian Coder",
            status: "created", detail: "vmid=101 ip=10.0.0.5" },
        ],
      });
    }
    if (method === "DELETE" && url.includes("/api/users/")) {
      return json({ detail: "User deleted" });
    }
    if (method === "PATCH" && /\/api\/users\/\d+$/.test(url)) {
      const id = Number(url.match(/\/api\/users\/(\d+)$/)![1]);
      const body = JSON.parse((init?.body as string) ?? "{}");
      return json(
        makeUser({
          id,
          username: "analyst.one@example.com",
          role: "user",
          user_id: "analyst-one",
          ...body,
        }),
      );
    }
    return { ok: false, status: 500, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** issue_024: the create-user card is collapsed behind an "Add user" button. */
async function openCreate() {
  await userEvent.click(await screen.findByRole("button", { name: /add user/i }));
  await screen.findByRole("heading", { name: /create user/i });
}

async function createUserAndOpenBanner() {
  await openCreate();
  await userEvent.type(screen.getByLabelText(/username/i), "newbie@example.com");
  await userEvent.type(screen.getByLabelText(/apps server hostname/i), "apps.example.com");
  await userEvent.click(screen.getByRole("button", { name: /^create user$/i }));
  // Banner shows the generated password.
  await screen.findByText("Generated-Pass-123");
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (document as unknown as { execCommand?: unknown }).execCommand;
});

/** Click a User Management sub-tab by its visible label. */
async function openTab(label: RegExp) {
  await userEvent.click(await screen.findByRole("button", { name: label }));
}

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

    const analystCard = (
      await screen.findByText("analyst", { selector: ".user-name" })
    ).closest("article");
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
    await openTab(/Bundle Templates/i);

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

  it("creates a user with an apps server IP", async () => {
    const fetchMock = stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 99, role: "admin" })} />);

    await openCreate();
    await userEvent.type(screen.getByLabelText(/username/i), "ipuser@example.com");
    await userEvent.type(screen.getByLabelText(/apps server ip/i), "10.0.0.8");
    await userEvent.click(screen.getByRole("button", { name: /^create user$/i }));

    const createCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/api/users") && (init?.method ?? "GET") === "POST",
    );
    expect(JSON.parse((createCall![1] as RequestInit).body as string)).toMatchObject({
      username: "ipuser@example.com",
      apps_server: "",
      apps_server_ip: "10.0.0.8",
    });
  });

  it("provisions every server template by default and shows the summary", async () => {
    const fetchMock = stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 99, role: "admin" })} />);

    await openCreate();
    // The template toggle is pre-selected (default ON).
    const toggle = await screen.findByLabelText(/Debian Coder \(LXC\)/i);
    expect(toggle).toBeChecked();

    await userEvent.type(
      screen.getByLabelText(/username/i),
      "newbie@example.com",
    );
    await userEvent.type(
      screen.getByLabelText(/apps server hostname/i),
      "apps.example.com",
    );
    await userEvent.click(screen.getByRole("button", { name: /^create user$/i }));

    const createCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/api/users") && (init?.method ?? "GET") === "POST",
    );
    expect(
      JSON.parse((createCall![1] as RequestInit).body as string),
    ).toMatchObject({ provision_templates: [7] });

    // The credential banner reports the per-template provisioning outcome.
    expect(await screen.findByText(/server provisioning/i)).toBeInTheDocument();
    expect(screen.getByText(/vmid=101 ip=10\.0\.0\.5/)).toBeInTheDocument();
  });

  it("offers explicit host and IP bundle mapping values", async () => {
    stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    await openTab(/Bundle Templates/i);

    await screen.findByRole("heading", { name: /bundle templates/i });
    expect(screen.getByRole("option", { name: /apps server host$/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /apps server ip/i })).toBeInTheDocument();
  });

  it("shows the derived user id below each username", async () => {
    stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);

    const userCards = await screen.findAllByRole("article");
    const analystCard = userCards.find((card) =>
      within(card).queryByText("analyst.one@example.com"),
    );
    expect(analystCard).toBeDefined();
    expect(within(analystCard!).getByText("analyst-one")).toBeInTheDocument();
  });

  it("collapses user cards by default and expands on demand (issue_025)", async () => {
    const fetchMock = stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);

    const analystCard = (
      await screen.findByText("analyst", { selector: ".user-name" })
    ).closest("article")!;
    // Collapsed: the user's servers panel is not mounted, so no per-user
    // servers fetch happened, and no "Create server"/servers UI is present.
    expect(
      fetchMock.mock.calls.some((c) => /\/api\/users\/\d+\/servers$/.test(String(c[0]))),
    ).toBe(false);
    expect(within(analystCard).queryByText(/add server/i)).toBeNull();

    // Expanding mounts the servers panel (which triggers the servers fetch).
    await userEvent.click(
      within(analystCard).getByRole("button", { name: /^expand$/i }),
    );
    await screen.findByText("analyst", { selector: ".user-name" });
    expect(
      within(analystCard).getByRole("button", { name: /^collapse$/i }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((c) =>
          /\/api\/users\/2\/servers$/.test(String(c[0])),
        ),
      ).toBe(true),
    );
  });
});

describe("UserManagement apps-server selection (issue_017)", () => {
  function stubWithAppsServer() {
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const json = (payload: unknown) =>
        ({ ok: true, status: 200, json: async () => payload }) as Response;
      if (method === "GET" && url.endsWith("/api/users")) return json([]);
      if (method === "GET" && url.endsWith("/api/teams")) return json([]);
      if (method === "GET" && url.endsWith("/api/settings/bundle-templates"))
        return json([]);
      if (method === "GET" && url.endsWith("/api/account/server-templates"))
        return json([]);
      if (method === "GET" && url.endsWith("/api/settings/server-templates"))
        return json([
          { id: 7, vmid: 9001, name: "apps-lxc", kind: "lxc",
            admin_ssh_key_path: "", admin_ssh_key_id: null, main_os_user: "",
            enable_sudo: true, enable_trusted_access: true,
            is_apps_server: true },
        ]);
      if (method === "POST" && url.endsWith("/api/users"))
        return json({ user: makeUser({ id: 2 }), password: "P", provisioning: [] });
      return { ok: false, status: 500, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("requires selecting a default apps server when apps servers exist", async () => {
    const fetchMock = stubWithAppsServer();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    await openCreate();
    await userEvent.type(
      screen.getByLabelText(/username/i),
      "newbie@example.com",
    );
    // Submit is blocked until an apps server is chosen.
    expect(
      screen.getByRole("button", { name: /^create user$/i }),
    ).toBeDisabled();

    await userEvent.selectOptions(
      screen.getByLabelText(/default apps server/i),
      "apps-lxc",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /^create user$/i }),
    );
    const post = fetchMock.mock.calls.find(
      (c) =>
        String(c[0]).endsWith("/api/users") &&
        (c[1]?.method ?? "").toUpperCase() === "POST",
    );
    expect(JSON.parse(post![1]!.body as string)).toMatchObject({
      apps_server: "apps-lxc",
    });
  });

  it("warns and stays optional when no apps servers exist", async () => {
    // Default stubUsers returns only a non-apps-server template.
    stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    await openCreate();
    expect(
      screen.getByText(/custom apps server is required to create/i),
    ).toBeInTheDocument();
    // No dropdown; the custom hostname field is present.
    expect(screen.queryByLabelText(/default apps server/i)).toBeNull();
    expect(screen.getByLabelText(/apps server hostname/i)).toBeInTheDocument();
  });
});

describe("UserManagement provisioning progress (issue_018)", () => {
  it("shows an indeterminate progress card while creating, then hides it", async () => {
    let releasePost: (() => void) | null = null;
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const json = (payload: unknown) =>
        ({ ok: true, status: 200, json: async () => payload }) as Response;
      if (method === "GET" && url.endsWith("/api/users")) return json([]);
      if (method === "GET" && url.endsWith("/api/teams")) return json([]);
      if (method === "GET" && url.endsWith("/api/settings/bundle-templates"))
        return json([]);
      if (method === "GET" && url.endsWith("/api/account/server-templates"))
        return json([]);
      if (method === "GET" && url.endsWith("/api/settings/server-templates"))
        return json([]);
      if (method === "POST" && url.endsWith("/api/users")) {
        await new Promise<void>((resolve) => {
          releasePost = resolve;
        });
        return json({
          user: makeUser({ id: 2, username: "newbie@example.com" }),
          password: "P",
          provisioning: [],
        });
      }
      return { ok: false, status: 500, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    await openCreate();
    await userEvent.type(
      screen.getByLabelText(/username/i),
      "newbie@example.com",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /^create user$/i }),
    );

    // While the POST is pending, the progress card is shown.
    expect(
      await screen.findByRole("progressbar", { name: /creating user/i }),
    ).toBeInTheDocument();

    // Release the request; the card disappears afterward.
    await waitFor(() => expect(releasePost).not.toBeNull());
    (releasePost as unknown as () => void)();
    await waitFor(() =>
      expect(
        screen.queryByRole("progressbar", { name: /creating user/i }),
      ).toBeNull(),
    );
  });
});

describe("UserManagement username edit", () => {
  it("shows an editable sign-in email alongside the immutable user ID", async () => {
    stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    const row = (
      await screen.findByText("analyst.one@example.com", { selector: ".user-name" })
    ).closest("article") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /^edit$/i }));
    const usernameField = within(row).getByLabelText(/sign-in email/i);
    expect(usernameField).toHaveValue("analyst.one@example.com");
    expect(
      within(row).getAllByText("analyst-one").length,
    ).toBeGreaterThan(0);
  });

  it("submits a renamed sign-in email and keeps the account's other fields", async () => {
    const fetchMock = stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    const row = (
      await screen.findByText("analyst.one@example.com", { selector: ".user-name" })
    ).closest("article") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /^edit$/i }));
    const usernameField = within(row).getByLabelText(/sign-in email/i);
    await userEvent.clear(usernameField);
    await userEvent.type(usernameField, "renamed@example.com");
    await userEvent.click(
      within(row).getByRole("button", { name: /save changes/i }),
    );
    const patchCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/api/users/3") &&
        (init?.method ?? "GET").toUpperCase() === "PATCH",
    );
    expect(patchCall).toBeDefined();
    const body = JSON.parse((patchCall![1] as RequestInit).body as string);
    expect(body.username).toBe("renamed@example.com");
  });

  it("allows saving a username-only change without an apps server set", async () => {
    stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    // "analyst" (id 2) has no apps server configured.
    const row = (
      await screen.findByText("analyst", { selector: ".user-name" })
    ).closest("article") as HTMLElement;
    await userEvent.click(within(row).getByRole("button", { name: /^edit$/i }));
    const usernameField = within(row).getByLabelText(/sign-in email/i);
    await userEvent.clear(usernameField);
    await userEvent.type(usernameField, "analyst2@example.com");
    expect(
      within(row).getByRole("button", { name: /save changes/i }),
    ).not.toBeDisabled();
  });
});

describe("UserManagement create-servers toggle", () => {
  it("defaults to creating servers with every template preselected", async () => {
    stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    await openCreate();
    const toggle = await screen.findByRole("checkbox", {
      name: /create servers for this user/i,
    });
    expect(toggle).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /debian coder/i }),
    ).toBeChecked();
  });

  it("clears template selection and hides the list when disabled", async () => {
    const fetchMock = stubUsers();
    render(<UserManagement currentUser={makeUser({ id: 1, role: "admin" })} />);
    await openCreate();
    await userEvent.click(
      await screen.findByRole("checkbox", { name: /create servers for this user/i }),
    );
    expect(
      screen.queryByRole("checkbox", { name: /debian coder/i }),
    ).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/username/i), "noservers@example.com");
    await userEvent.type(
      screen.getByLabelText(/apps server hostname/i),
      "apps.example.com",
    );
    await userEvent.click(screen.getByRole("button", { name: /^create user$/i }));

    const postCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/api/users") &&
        (init?.method ?? "GET").toUpperCase() === "POST",
    );
    expect(postCall).toBeDefined();
    const body = JSON.parse((postCall![1] as RequestInit).body as string);
    expect(body.provision_templates).toEqual([]);
  });
});

