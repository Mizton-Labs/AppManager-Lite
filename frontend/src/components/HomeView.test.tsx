import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { HomeView } from "./HomeView";
import { makeApp } from "../test/fixtures";
import type { Application } from "../types";

/**
 * Stub the two endpoints HomeView calls: the team-scoped shared listing
 * (`/api/applications`) and the caller's own apps (`/api/applications/mine`).
 */
function stubHome(visible: Application[], mine: Application[]) {
  const fetchMock = vi.fn(async (input: string) => {
    const url = String(input);
    const payload = url.includes("/applications/mine") ? mine : visible;
    return { ok: true, status: 200, json: async () => payload } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function stubHomeError() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: false,
    status: 500,
    json: async () => ({ detail: "boom" }),
  } as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("HomeView", () => {
  it("renders shared application cards returned by the API", async () => {
    const shared = makeApp({ id: 1, name: "Hunt Workbench" });
    stubHome([shared], []);
    render(<HomeView teams={["Threat Hunting"]} />);

    const link = await screen.findByRole("link", { name: /Hunt Workbench/ });
    expect(link).toHaveAttribute("href", "https://example.com/hunt");
  });

  it("splits owned applications out of the shared section", async () => {
    const ownedApp = makeApp({ id: 10, name: "My Tool" });
    const otherApp = makeApp({ id: 20, name: "Team Tool" });
    // The shared listing returns both; `mine` marks id 10 as owned.
    stubHome([ownedApp, otherApp], [ownedApp]);
    render(<HomeView teams={["Threat Hunting"]} />);

    const sharedHeading = await screen.findByRole("heading", {
      name: /available shared applications/i,
    });
    const ownedHeading = screen.getByRole("heading", {
      name: /my applications/i,
    });
    const sharedSection = sharedHeading.closest("section") as HTMLElement;
    const ownedSection = ownedHeading.closest("section") as HTMLElement;

    expect(within(sharedSection).getByText("Team Tool")).toBeInTheDocument();
    expect(within(sharedSection).queryByText("My Tool")).not.toBeInTheDocument();
    expect(within(ownedSection).getByText("My Tool")).toBeInTheDocument();
    expect(within(ownedSection).queryByText("Team Tool")).not.toBeInTheDocument();
  });

  it("shows a publisher-team badge on each card", async () => {
    stubHome([makeApp({ id: 1, teams: ["Threat Hunting"], publisher_team: "Red Team" })], []);
    render(<HomeView teams={["Threat Hunting"]} />);

    await screen.findByRole("link", { name: /Hunt Workbench/ });
    expect(screen.getByText("Team: Red Team")).toBeInTheDocument();
    expect(screen.queryByText("Threat Hunting")).toBeNull();
  });

  it("shows a no-shared-applications message when empty", async () => {
    stubHome([], []);
    render(<HomeView teams={["Threat Hunting"]} />);

    expect(
      await screen.findByText(/No shared applications are available/i),
    ).toBeInTheDocument();
  });

  it("prompts to contact an administrator when no teams are assigned", async () => {
    stubHome([], []);
    render(<HomeView teams={[]} />);

    expect(
      await screen.findByText(/No teams are assigned/i),
    ).toBeInTheDocument();
  });

  it("surfaces an error when the request fails", async () => {
    stubHomeError();
    render(<HomeView teams={["Threat Hunting"]} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
