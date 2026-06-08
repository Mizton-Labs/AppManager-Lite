import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppCard } from "./AppCard";
import { makeApp } from "../test/fixtures";

describe("AppCard", () => {
  it("links to the application URL and opens safely in a new tab", () => {
    render(<AppCard app={makeApp()} />);

    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "https://example.com/hunt");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("Hunt Workbench")).toBeInTheDocument();
    expect(screen.getByText("Run hunting queries.")).toBeInTheDocument();
  });

  it("falls back to a monogram when no icon is provided", () => {
    render(<AppCard app={makeApp({ name: "Hunt Workbench", icon_url: "" })} />);
    expect(screen.getByText("HW")).toBeInTheDocument();
  });

  it("renders the icon image when an icon URL is provided", () => {
    const { container } = render(
      <AppCard app={makeApp({ icon_url: "https://example.com/icon.svg" })} />,
    );
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    expect(img).toHaveAttribute("src", "https://example.com/icon.svg");
  });

  it("resolves a relative default-logo path against the document base", () => {
    const { container } = render(
      <AppCard app={makeApp({ icon_url: "logos/red-team-2.svg" })} />,
    );
    const img = container.querySelector("img");
    expect(img).not.toBeNull();
    // jsdom base URI is http://localhost/, so the relative path resolves absolute.
    expect(img?.getAttribute("src")).toMatch(
      /^https?:\/\/.+\/logos\/red-team-2\.svg$/,
    );
  });

  it("passes an inline data-URI logo through unchanged", () => {
    const data = "data:image/png;base64,iVBORw0KGgo=";
    const { container } = render(<AppCard app={makeApp({ icon_url: data })} />);
    expect(container.querySelector("img")).toHaveAttribute("src", data);
  });

  it("shows team-scope badges for each team", () => {
    render(
      <AppCard
        app={makeApp({ teams: ["Threat Hunting", "Red Team"] })}
      />,
    );
    expect(screen.getByText("Threat Hunting")).toBeInTheDocument();
    expect(screen.getByText("Red Team")).toBeInTheDocument();
  });
});
