import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AuditView } from "./AuditView";
import type { AuditEntry, NavigationActivityEntry } from "../types";

function entry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: 1,
    created_at: "2026-06-05T10:00:00Z",
    category: "application",
    action: "create",
    actor_username: "admin",
    target_type: "application",
    target_id: 5,
    target_name: "Some App",
    detail: "teams=['Red Team'] approval=approved",
    ...overrides,
  };
}

/** Stub the audit endpoint, returning per-category payloads. */
function stubAudit(byCategory: Record<string, AuditEntry[]>) {
  const fetchMock = vi.fn(async (input: string) => {
    const url = String(input);
    const match = url.match(/category=([^&]+)/);
    const category = match ? decodeURIComponent(match[1]) : "application";
    return {
      ok: true,
      status: 200,
      json: async () => byCategory[category] ?? [],
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("AuditView", () => {
  it("loads the application category by default", async () => {
    stubAudit({ application: [entry({ target_name: "Audited App" })] });
    render(<AuditView />);

    expect(await screen.findByText("Audited App")).toBeInTheDocument();
    expect(screen.getByText("create")).toBeInTheDocument();
  });

  it("switches category when a tab is selected", async () => {
    const fetchMock = stubAudit({
      application: [entry({ target_name: "App One" })],
      user: [
        entry({
          id: 2,
          category: "user",
          action: "login",
          target_name: "alice",
          detail: "",
        }),
      ],
    });
    render(<AuditView />);
    await screen.findByText("App One");

    await userEvent.click(screen.getByRole("button", { name: /user activity/i }));

    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByText("login")).toBeInTheDocument();
    // The user category was requested.
    expect(
      fetchMock.mock.calls.some(([u]) => String(u).includes("category=user")),
    ).toBe(true);
  });

  it("shows an empty-state message when a category has no events", async () => {
    stubAudit({ application: [] });
    render(<AuditView />);

    expect(
      await screen.findByText(/No activity recorded in this category yet/i),
    ).toBeInTheDocument();
  });

  it("shows the Navigation activity tab with section/visit-count data", async () => {
    const navEntry: NavigationActivityEntry = {
      id: 1,
      actor_username: "admin",
      destination: "servers",
      first_seen_at: "2026-06-05T10:00:00Z",
      last_seen_at: "2026-06-05T10:05:00Z",
      visit_count: 3,
    };
    const fetchMock = vi.fn(async (input: string) => {
      const url = String(input);
      if (url.includes("/api/audit/navigation")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ items: [navEntry], total: 1, offset: 0, limit: 50 }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => [] } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuditView />);
    await userEvent.click(
      screen.getByRole("button", { name: /navigation activity/i }),
    );
    expect(await screen.findByText("servers")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
  });

  it("paginates navigation activity at 50 per page", async () => {
    const fetchMock = vi.fn(async (input: string) => {
      const url = String(input);
      if (url.includes("/api/audit/navigation")) {
        const parsed = new URL(url, "http://localhost");
        const offset = Number(parsed.searchParams.get("offset") ?? "0");
        const item = (n: number) => ({
          id: n,
          actor_username: "admin",
          destination: "home",
          first_seen_at: "2026-06-05T10:00:00Z",
          last_seen_at: "2026-06-05T10:00:00Z",
          visit_count: 1,
        });
        const items =
          offset === 0
            ? Array.from({ length: 50 }, (_, i) => item(i))
            : [item(50)];
        return {
          ok: true,
          status: 200,
          json: async () => ({ items, total: 51, offset, limit: 50 }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => [] } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<AuditView />);
    await userEvent.click(
      screen.getByRole("button", { name: /navigation activity/i }),
    );
    expect(await screen.findByText("Page 1 of 2")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    await waitFor(() => {
      const navCall = fetchMock.mock.calls.find(([u]) => String(u).includes("offset=50"));
      expect(navCall).toBeDefined();
    });
    expect(await screen.findByText("Page 2 of 2")).toBeInTheDocument();
  });
});
