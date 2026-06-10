import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TeamView } from "./TeamView";
import { makeApp } from "../test/fixtures";

function stubFetch(payload: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => payload,
  } as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const SAMPLE = makeApp({
  id: 5,
  name: "Adversary Emulation Range",
  description: "Plan offensive exercises.",
  url: "https://example.com/range",
  teams: ["Red Team"],
});

function renderAt(path: string, teams: readonly string[]) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/teams/:slug" element={<TeamView teams={teams} />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("TeamView", () => {
  it("fetches and renders applications for a member's team", async () => {
    const fetchMock = stubFetch([SAMPLE]);
    renderAt("/teams/red-team", ["Red Team"]);

    expect(
      await screen.findByRole("link", { name: /Adversary Emulation Range/ }),
    ).toBeInTheDocument();
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("applications?publisher_team=Red%20Team");
  });

  it("shows unknown team for a slug outside the visible team list", () => {
    // Team visibility is data-driven: a slug not in the caller's team list is
    // treated as unknown (the sidebar never links non-visible teams).
    const fetchMock = stubFetch([]);
    renderAt("/teams/threat-hunting", ["Red Team"]);

    expect(screen.getByText(/Unknown team/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports an unknown team for an unrecognised slug", () => {
    stubFetch([]);
    renderAt("/teams/not-a-team", ["Red Team"]);

    expect(screen.getByText(/Unknown team/i)).toBeInTheDocument();
  });

  it("shows an empty-state message when the team has no applications", async () => {
    stubFetch([]);
    renderAt("/teams/red-team", ["Red Team"]);

    expect(
      await screen.findByText(/No apps are available or shared from this team/i),
    ).toBeInTheDocument();
  });
});
