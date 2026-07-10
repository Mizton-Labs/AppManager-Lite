import { afterEach, describe, expect, it, vi } from "vitest";
import { render as rtlRender, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import userEvent from "@testing-library/user-event";
import { AccountPanel } from "./AccountPanel";
import { ThemeProvider } from "../theme";
import { makeUser } from "../test/fixtures";

function render(ui: ReactElement) {
  return rtlRender(<ThemeProvider>{ui}</ThemeProvider>);
}

function jsonResponse(payload: unknown): Response {
  return { ok: true, status: 200, json: async () => payload } as Response;
}

function textResponse(payload: string, filename: string): Response {
  return {
    ok: true,
    status: 200,
    text: async () => payload,
    headers: new Headers({
      "content-disposition": `attachment; filename="${filename}"`,
    }),
  } as Response;
}

function blobResponse(filename: string): Response {
  return {
    ok: true,
    status: 200,
    blob: async () =>
      new Blob([new Uint8Array([1, 2, 3])], { type: "application/zip" }),
    headers: new Headers({
      "content-disposition": `attachment; filename="${filename}"`,
    }),
  } as Response;
}

const sshKey = {
  user_id: "analyst",
  public_key: "ssh-ed25519 AAAATESTKEY analyst@example.com",
  generated_at: "2026-07-07 00:00:00",
};

function stubAccount(overrides: {
  onRegenerate?: () => unknown;
} = {}) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/account/bundles")) {
      return jsonResponse([
        { id: 7, name: "Shell profile", description: "Handy shell config" },
      ]);
    }
    if (url.endsWith("/api/account/bundles/7/download")) {
      return blobResponse("shell-profile.zip");
    }
    if (url.endsWith("/api/account/ssh-key/regenerate")) {
      const base = overrides.onRegenerate ? overrides.onRegenerate() : sshKey;
      return jsonResponse({ rotation: [], ...(base as object) });
    }
    if (url.includes("/api/account/ssh-key/download?part=private")) {
      return textResponse(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n",
        "id_ed25519",
      );
    }
    if (url.includes("/api/account/ssh-key/download?part=public")) {
      return textResponse(`${sshKey.public_key}\n`, "id_ed25519.pub");
    }
    if (url.endsWith("/api/account/ssh-key")) {
      return jsonResponse(sshKey);
    }
    if (url.endsWith("/api/account/server-access")) {
      return jsonResponse({ can_create: false, reason: "" });
    }
    if (url.endsWith("/api/account/server-templates")) {
      return jsonResponse([]);
    }
    if (/\/api\/users\/\d+\/servers$/.test(url)) {
      return jsonResponse([]);
    }
    return jsonResponse({ detail: `unexpected ${init?.method ?? "GET"} ${url}` });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function stubDownloads() {
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:file"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  return click;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AccountPanel", () => {
  it("renders all account cards without tabs", async () => {
    stubAccount();
    render(
      <AccountPanel
        user={makeUser({ username: "analyst@example.com" })}
        onPasswordChanged={() => undefined}
      />,
    );
    // No sub-tab buttons; every card heading is visible at once.
    expect(screen.queryByRole("button", { name: "User Info" })).toBeNull();
    expect(screen.getByText("Profile")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /my servers/i })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /bundle downloads/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^ssh key$/i })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /change password/i }),
    ).toBeInTheDocument();
    // issue_019: the theme selector now lives in the Account section.
    expect(
      screen.getByRole("heading", { name: /appearance/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Theme")).toBeInTheDocument();
  });

  it("lists and downloads account bundles", async () => {
    const click = stubDownloads();
    const fetchMock = stubAccount();

    render(
      <AccountPanel
        user={makeUser({ username: "analyst@example.com" })}
        onPasswordChanged={() => undefined}
      />,
    );

    expect(await screen.findByText("Shell profile")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /bundle downloads/i }),
    ).toBeInTheDocument();
    // The selected bundle's description is shown under the dropdown.
    expect(await screen.findByText("Handy shell config")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /download file/i }));

    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).endsWith("/api/account/bundles/7/download"),
      ),
    ).toBe(true);
    expect(click).toHaveBeenCalled();
  });

  it("shows the derived user id in the profile", async () => {
    stubAccount();
    render(
      <AccountPanel
        user={makeUser({ username: "john.doe@example.com", user_id: "john-doe" })}
        onPasswordChanged={() => undefined}
      />,
    );
    expect(screen.getByText("User ID")).toBeInTheDocument();
    expect(screen.getByText("john-doe")).toBeInTheDocument();
  });

  it("shows the account SSH public key and downloads the private key", async () => {
    const click = stubDownloads();
    const fetchMock = stubAccount();

    render(
      <AccountPanel
        user={makeUser({ username: "analyst@example.com" })}
        onPasswordChanged={() => undefined}
      />,
    );

    expect(await screen.findByText(sshKey.public_key)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /download private key/i }),
    );

    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/api/account/ssh-key/download?part=private"),
      ),
    ).toBe(true);
    expect(click).toHaveBeenCalled();
  });

  it("regenerates the SSH key only after an explicit confirmation", async () => {
    const regenerated = {
      ...sshKey,
      public_key: "ssh-ed25519 NEWKEY analyst",
      rotation: [
        {
          server: "coder box",
          ip_address: "10.0.7.42",
          status: "updated",
          detail: "key rotated",
        },
        { server: "no-ip", ip_address: "", status: "skipped", detail: "no IP" },
      ],
    };
    const fetchMock = stubAccount({ onRegenerate: () => regenerated });

    render(
      <AccountPanel
        user={makeUser({ username: "analyst@example.com" })}
        onPasswordChanged={() => undefined}
      />,
    );

    await screen.findByText(sshKey.public_key);
    await userEvent.click(screen.getByRole("button", { name: /regenerate key/i }));

    // No request yet; a warning plus a confirm button appear instead.
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).endsWith("/api/account/ssh-key/regenerate"),
      ),
    ).toBe(false);
    expect(screen.getByText(/cannot be undone/i)).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /confirm regenerate/i }),
    );

    expect(
      fetchMock.mock.calls.some(
        ([url, init]) =>
          String(url).endsWith("/api/account/ssh-key/regenerate") &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(true);
    expect(await screen.findByText(regenerated.public_key)).toBeInTheDocument();

    // Per-server rotation summary with statuses.
    expect(
      screen.getByRole("heading", { name: /key rotation summary/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("updated")).toBeInTheDocument();
    expect(screen.getByText("skipped")).toBeInTheDocument();
    expect(screen.getByText(/1 updated of 2/i)).toBeInTheDocument();
  });
});
