import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TeamManagement } from "./TeamManagement";
import { makeTeam } from "../test/fixtures";

/**
 * Stub the team endpoints. The mock keeps an in-memory list so create / delete /
 * reorder are observable through the re-fetched GET /api/teams.
 */
function stubTeams(initial = [makeTeam({ id: 1, name: "Red Team" })]) {
  let teams = [...initial];
  const calls: { url: string; method: string; body: unknown }[] = [];

  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const json = (payload: unknown) =>
      ({ ok: true, status: 200, json: async () => payload }) as Response;

    if (method === "GET" && /\/api\/teams\b/.test(url)) {
      return json(teams);
    }
    if (method === "POST" && url.endsWith("/api/settings/teams/reorder")) {
      const order: number[] = body.team_ids;
      teams = order.map(
        (id, index) =>
          ({ ...teams.find((t) => t.id === id)!, sort_order: index }),
      );
      return json(teams);
    }
    if (method === "POST" && url.endsWith("/api/settings/teams")) {
      const created = {
        id: teams.length + 1,
        name: body.name,
        icon: body.icon ?? "",
        sort_order: teams.length,
      };
      teams = [...teams, created];
      return json(created);
    }
    const patch = url.match(/\/api\/settings\/teams\/(\d+)$/);
    if (method === "PATCH" && patch) {
      const id = Number(patch[1]);
      teams = teams.map((t) =>
        t.id === id ? { ...t, ...body } : t,
      );
      return json(teams.find((t) => t.id === id));
    }
    if (method === "DELETE" && patch) {
      const id = Number(patch[1]);
      teams = teams.filter((t) => t.id !== id);
      return json({ detail: "Team deleted" });
    }
    return { ok: false, status: 500, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls: () => calls };
}

afterEach(() => vi.unstubAllGlobals());

describe("TeamManagement", () => {
  it("lists existing teams", async () => {
    stubTeams([
      makeTeam({ id: 1, name: "Alpha" }),
      makeTeam({ id: 2, name: "Bravo", sort_order: 1 }),
    ]);
    render(<TeamManagement />);

    const list = await screen.findByRole("list", { name: "Teams" });
    expect(within(list).getByText("Alpha")).toBeInTheDocument();
    expect(within(list).getByText("Bravo")).toBeInTheDocument();
  });

  it("creates a team via the add form", async () => {
    const { calls } = stubTeams([]);
    render(<TeamManagement />);

    await screen.findByText(/No teams yet/i);
    await userEvent.type(
      screen.getByRole("textbox", { name: /team name/i }),
      "Platform",
    );
    await userEvent.click(screen.getByRole("button", { name: /add team/i }));

    const created = calls().find(
      (c) => c.method === "POST" && c.url.endsWith("/api/settings/teams"),
    );
    expect(created?.body).toMatchObject({ name: "Platform" });
    // The list refreshes and shows the new team.
    expect(
      await screen.findByText("Platform"),
    ).toBeInTheDocument();
  });

  it("selects a catalogue icon when creating a team", async () => {
    const { calls } = stubTeams([]);
    render(<TeamManagement />);

    await screen.findByText(/No teams yet/i);
    await userEvent.type(
      screen.getByRole("textbox", { name: /team name/i }),
      "Network",
    );
    // Pick the "Network" catalogue icon (button titled by its label).
    await userEvent.click(screen.getByRole("button", { name: "Network" }));
    await userEvent.click(screen.getByRole("button", { name: /add team/i }));

    const created = calls().find(
      (c) => c.method === "POST" && c.url.endsWith("/api/settings/teams"),
    );
    expect(created?.body).toMatchObject({
      name: "Network",
      icon: "team-icons/network.svg",
    });
  });

  it("selects a cybersecurity catalogue icon when creating a team", async () => {
    const { calls } = stubTeams([]);
    render(<TeamManagement />);

    await screen.findByText(/No teams yet/i);
    await userEvent.type(
      screen.getByRole("textbox", { name: /team name/i }),
      "Red Team",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Offensive Security 1" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /add team/i }));

    const created = calls().find(
      (c) => c.method === "POST" && c.url.endsWith("/api/settings/teams"),
    );
    expect(created?.body).toMatchObject({
      name: "Red Team",
      icon: "team-icons/offensive-security-1.svg",
    });
  });

  it("renames a team", async () => {
    const { calls } = stubTeams([makeTeam({ id: 7, name: "Old" })]);
    render(<TeamManagement />);

    await screen.findByText("Old");
    await userEvent.click(screen.getByRole("button", { name: /^Edit$/ }));
    const input = screen.getByRole("textbox", { name: /team name for Old/i });
    await userEvent.clear(input);
    await userEvent.type(input, "New");
    await userEvent.click(screen.getByRole("button", { name: /^Save$/ }));

    const patch = calls().find((c) => c.method === "PATCH");
    expect(patch?.url).toMatch(/\/api\/settings\/teams\/7$/);
    expect(patch?.body).toMatchObject({ name: "New" });
  });

  it("deletes a team after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = stubTeams([makeTeam({ id: 3, name: "Temp" })]);
    render(<TeamManagement />);

    await screen.findByText("Temp");
    await userEvent.click(screen.getByRole("button", { name: /^Delete$/ }));

    const del = calls().find((c) => c.method === "DELETE");
    expect(del?.url).toMatch(/\/api\/settings\/teams\/3$/);
  });

  it("reorders teams with the move-down control", async () => {
    const { calls } = stubTeams([
      makeTeam({ id: 1, name: "First" }),
      makeTeam({ id: 2, name: "Second", sort_order: 1 }),
    ]);
    render(<TeamManagement />);

    await screen.findByText("First");
    await userEvent.click(
      screen.getByRole("button", { name: /move First down/i }),
    );

    const reorder = calls().find((c) =>
      c.url.endsWith("/api/settings/teams/reorder"),
    );
    expect(reorder?.body).toMatchObject({ team_ids: [2, 1] });
  });
});
