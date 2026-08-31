import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppStatisticsView, MultiLineChart } from "./AppStatisticsView";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("MultiLineChart", () => {
  it("renders a visible line and point when only one day has activity", () => {
    const points = Array.from({ length: 7 }, (_, index) => ({
      date: `2026-07-${String(9 + index).padStart(2, "0")}`,
      launches: index === 6 ? 3 : 0,
      unique_users: 0,
    }));
    const { container } = render(
      <MultiLineChart
        series={[{ application_id: 1, name: "ThreatBox", launches: 3, points }]}
      />,
    );
    const path = container.querySelector(".stats-series-line");
    expect(path?.getAttribute("d")).toContain("L");
    expect(container.querySelectorAll(".stats-series-point")).toHaveLength(1);
    expect(screen.getByText("ThreatBox")).toBeInTheDocument();
  });

  it("shows an empty state without active application series", () => {
    render(<MultiLineChart series={[]} />);
    expect(screen.getByText(/No application activity/i)).toBeInTheDocument();
  });
});

describe("AppStatisticsView", () => {
  it("shows authorized alias visit totals separately from launches", async () => {
    const fetchMock = vi.fn(async (input: string) => {
      const url = String(input);
      const json = (payload: unknown) =>
        ({ ok: true, status: 200, json: async () => payload }) as Response;
      if (url.endsWith("/api/application-statistics-settings")) {
        return json({ show_app_statistics: false });
      }
      if (url.includes("/api/application-statistics")) {
        return json({
          days: 30, launches: 12, unique_users: 3, favorites: 2,
          trend: [], applications: [
            {
              application_id: 1, name: "Grafana", launches: 12,
              unique_users: 3, favorites: 2, visits_7d: 4,
              alias_visits: 40, unique_alias_users: 3,
              anonymous_alias_visits: 10,
            },
          ],
          app_trends: [], user_activity: [],
          alias_visits: 40, unique_alias_users: 3, anonymous_alias_visits: 10,
        });
      }
      return { ok: false, status: 500, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AppStatisticsView />);

    expect(await screen.findByText("Authorized alias visits")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("40 alias visits")).toBeInTheDocument();
    expect(screen.getByText("12 launches")).toBeInTheDocument();
  });

  function stubFullStats() {
    const fetchMock = vi.fn(async (input: string) => {
      const url = String(input);
      const json = (payload: unknown) =>
        ({ ok: true, status: 200, json: async () => payload }) as Response;
      if (url.endsWith("/api/application-statistics-settings")) {
        return json({ show_app_statistics: false });
      }
      if (url.includes("/api/application-statistics")) {
        return json({
          days: 30, launches: 12, unique_users: 3, favorites: 2,
          trend: [], applications: [
            {
              application_id: 1, name: "Grafana", launches: 12,
              unique_users: 3, favorites: 2, visits_7d: 4,
              alias_visits: 40, unique_alias_users: 3,
              anonymous_alias_visits: 10,
            },
          ],
          app_trends: [], user_activity: [],
          alias_visits: 40, unique_alias_users: 3, anonymous_alias_visits: 10,
          launch_users: [
            { user_id: "morris", launches: 8, applications_used: 2, active_days: 3, last_activity: "2026-07-20" },
          ],
          favorite_entries: [
            { application_id: 1, application_name: "Grafana", user_id: "nadia", starred_at: "2026-01-05" },
          ],
          alias_users: [
            {
              user_id: "morris", alias_visits: 30, applications_visited: 1,
              active_days: 4, last_visit: "2026-07-21",
              applications: [
                { application_id: 1, application_name: "Grafana", alias_visits: 30 },
              ],
            },
          ],
        });
      }
      return { ok: false, status: 500, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("shows all six KPI cards in one row and clicking one selects its tab", async () => {
    stubFullStats();
    render(<AppStatisticsView />);
    await screen.findByText("Authorized alias visits");

    expect(screen.getByText("Launches")).toBeInTheDocument();
    expect(screen.getByText("Unique launch users")).toBeInTheDocument();
    expect(screen.getByText("Current favorites")).toBeInTheDocument();
    expect(screen.getByText("Authorized alias visits")).toBeInTheDocument();
    expect(screen.getByText("Unique alias users")).toBeInTheDocument();
    expect(screen.getByText("Anonymous alias visits")).toBeInTheDocument();

    // Applications tab is the default.
    expect(screen.getByRole("button", { name: "Applications" })).toHaveAttribute(
      "aria-current",
      "true",
    );

    await userEvent.click(
      screen.getByRole("button", { name: /show current favorites details/i }),
    );
    expect(screen.getByRole("button", { name: "Favorites" })).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByText("nadia")).toBeInTheDocument();
  });

  it("shows the complete launch-users list on its tab", async () => {
    stubFullStats();
    render(<AppStatisticsView />);
    await screen.findByText("Authorized alias visits");
    await userEvent.click(screen.getByRole("button", { name: "Launch users" }));
    expect(screen.getByText("morris")).toBeInTheDocument();
    expect(screen.getByText("8 launches")).toBeInTheDocument();
  });

  it("shows current favorites unaffected by the period, with a snapshot note", async () => {
    stubFullStats();
    render(<AppStatisticsView />);
    await screen.findByText("Authorized alias visits");
    await userEvent.click(screen.getByRole("button", { name: "Favorites" }));
    expect(screen.getByText(/current snapshot/i)).toBeInTheDocument();
    expect(screen.getByText("Grafana")).toBeInTheDocument();
    expect(screen.getByText("nadia")).toBeInTheDocument();
  });

  it("shows authenticated alias users and an anonymous-visits note, never a unique anonymous user", async () => {
    stubFullStats();
    render(<AppStatisticsView />);
    await screen.findByText("Authorized alias visits");
    await userEvent.click(screen.getByRole("button", { name: "Alias visits" }));
    expect(screen.getByText("morris")).toBeInTheDocument();
    expect(screen.getByText("30 visits")).toBeInTheDocument();
    expect(screen.getByText(/10 anonymous visits/i)).toBeInTheDocument();
  });

  it("expands an alias-user row to show its per-application breakdown, and collapses on a second click", async () => {
    stubFullStats();
    render(<AppStatisticsView />);
    await screen.findByText("Authorized alias visits");
    await userEvent.click(screen.getByRole("button", { name: "Alias visits" }));
    const row = screen.getByRole("button", { name: /morris/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Grafana · 30 visits/)).toBeNull();

    await userEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Grafana · 30 visits/)).toBeInTheDocument();

    await userEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Grafana · 30 visits/)).toBeNull();
  });

  function stubManyAliasUsers(count: number) {
    const fetchMock = vi.fn(async (input: string) => {
      const url = String(input);
      const json = (payload: unknown) =>
        ({ ok: true, status: 200, json: async () => payload }) as Response;
      if (url.endsWith("/api/application-statistics-settings")) {
        return json({ show_app_statistics: false });
      }
      if (url.includes("/api/application-statistics")) {
        return json({
          days: 30, launches: 0, unique_users: 0, favorites: 0,
          trend: [], applications: [], app_trends: [], user_activity: [],
          alias_visits: 0, unique_alias_users: count, anonymous_alias_visits: 0,
          launch_users: [], favorite_entries: [],
          alias_users: Array.from({ length: count }, (_, i) => ({
            user_id: `user${i}`, alias_visits: count - i, applications_visited: 1,
            active_days: 1, last_visit: "2026-07-21", applications: [],
          })),
        });
      }
      return { ok: false, status: 500, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
  }

  it("paginates alias users at 10 per page", async () => {
    stubManyAliasUsers(11);
    render(<AppStatisticsView />);
    await screen.findByText("Authorized alias visits");
    await userEvent.click(screen.getByRole("button", { name: "Alias visits" }));
    expect(screen.getByText("user0")).toBeInTheDocument();
    expect(screen.queryByText("user10")).toBeNull();
    expect(screen.getByText("Page 1 of 2")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /^next$/i }));
    expect(await screen.findByText("user10")).toBeInTheDocument();
    expect(screen.queryByText("user0")).toBeNull();
  });

  it("omits alias-user pagination at 10 or fewer users", async () => {
    stubManyAliasUsers(10);
    render(<AppStatisticsView />);
    await screen.findByText("Authorized alias visits");
    await userEvent.click(screen.getByRole("button", { name: "Alias visits" }));
    expect(screen.getByText("user0")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: /alias users pagination/i })).toBeNull();
  });
});
