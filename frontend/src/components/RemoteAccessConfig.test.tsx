import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RemoteAccessConfig } from "./RemoteAccessConfig";
import type { SshKey } from "../types";

function stubKeys(initial: SshKey[] = []) {
  let keys = [...initial];
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const json = (payload: unknown, status = 200) =>
      ({ ok: status < 400, status, json: async () => payload }) as Response;

    if (url.endsWith("/api/settings/ssh-keys") && method === "GET") {
      return json(keys);
    }
    if (url.endsWith("/api/settings/ssh-keys") && method === "POST") {
      const body = JSON.parse(init?.body as string);
      const created: SshKey = {
        id: keys.length + 1,
        name: body.name,
        kind: body.kind,
        path: body.path ?? "",
        public_key: body.kind === "stored" ? "ssh-ed25519 AAAAKEY x" : "",
        fingerprint: body.kind === "stored" ? "SHA256:abc" : "",
        has_private_key: body.kind === "stored",
      };
      keys = [...keys, created];
      return json(created, 201);
    }
    if (url.includes("/api/settings/ssh-keys/") && method === "DELETE") {
      keys = keys.filter((k) => !url.endsWith(`/${k.id}`));
      return json({ detail: "SSH key deleted" });
    }
    return json({ detail: `unexpected ${method} ${url}` }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("RemoteAccessConfig", () => {
  it("adds a path-based key", async () => {
    const fetchMock = stubKeys();
    render(<RemoteAccessConfig />);

    await screen.findByRole("heading", { name: /add ssh key/i });
    await userEvent.type(screen.getByLabelText(/^name$/i), "proxy key");
    await userEvent.type(
      screen.getByLabelText(/key file path/i),
      "/data/keys/id_ed25519",
    );
    await userEvent.click(screen.getByRole("button", { name: /add key/i }));

    expect(await screen.findByText("proxy key")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(
      ([u, i]) =>
        String(u).endsWith("/api/settings/ssh-keys") &&
        (i as RequestInit | undefined)?.method === "POST",
    );
    expect(JSON.parse((call![1] as RequestInit).body as string)).toMatchObject({
      name: "proxy key",
      kind: "path",
      path: "/data/keys/id_ed25519",
    });
  });

  it("pastes a stored private key (sent as private_key, not path)", async () => {
    const fetchMock = stubKeys();
    render(<RemoteAccessConfig />);

    await screen.findByRole("heading", { name: /add ssh key/i });
    await userEvent.type(screen.getByLabelText(/^name$/i), "stored key");
    await userEvent.click(screen.getByLabelText(/paste a private key/i));
    await userEvent.type(
      screen.getByLabelText(/unencrypted openssh/i),
      "-----BEGIN OPENSSH PRIVATE KEY-----abc-----END-----",
    );
    await userEvent.click(screen.getByRole("button", { name: /add key/i }));

    expect(await screen.findByText("stored key")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(
      ([u, i]) =>
        String(u).endsWith("/api/settings/ssh-keys") &&
        (i as RequestInit | undefined)?.method === "POST",
    );
    const sent = JSON.parse((call![1] as RequestInit).body as string);
    expect(sent.kind).toBe("stored");
    expect(sent.private_key).toContain("BEGIN OPENSSH");
    expect(sent.path).toBeUndefined();
  });

  it("deletes a key after confirmation", async () => {
    stubKeys([
      {
        id: 1,
        name: "old key",
        kind: "path",
        path: "/k",
        public_key: "",
        fingerprint: "",
        has_private_key: false,
      },
    ]);
    render(<RemoteAccessConfig />);

    await screen.findByText("old key");
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await userEvent.click(
      screen.getByRole("button", { name: /confirm delete/i }),
    );
    expect(
      await screen.findByText(/no ssh keys registered yet/i),
    ).toBeInTheDocument();
  });
});
