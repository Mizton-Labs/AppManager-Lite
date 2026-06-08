import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AuditView } from "./AuditView";
import type { AuditEntry } from "../types";

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
});
