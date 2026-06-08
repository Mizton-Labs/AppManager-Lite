import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, apiBase, setCsrfToken } from "./api";

function mockFetch(body: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
  } as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("apiBase", () => {
  it("resolves to an absolute URL ending in /api/", () => {
    expect(apiBase()).toMatch(/\/api\/$/);
  });
});

describe("api request behavior", () => {
  beforeEach(() => setCsrfToken(null));
  afterEach(() => vi.unstubAllGlobals());

  it("omits the CSRF header on GET requests and sends cookies", async () => {
    const fetchMock = mockFetch({
      authenticated: false,
      enable_auth: true,
      user: null,
      csrf_token: null,
    });
    await api.getSession();
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("attaches the CSRF header on state-changing requests", async () => {
    setCsrfToken("token-123");
    const fetchMock = mockFetch({ user: { id: 1 }, password: "x" }, true, 201);
    await api.createUser({ username: "alice", role: "user", teams: [] });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/users$/);
    expect(init.method).toBe("POST");
    expect(init.headers["X-CSRF-Token"]).toBe("token-123");
  });

  it("raises ApiError carrying the server-provided detail", async () => {
    mockFetch({ detail: "Not allowed" }, false, 403);
    await expect(api.listUsers()).rejects.toMatchObject({
      status: 403,
      message: "Not allowed",
    });
  });
});
