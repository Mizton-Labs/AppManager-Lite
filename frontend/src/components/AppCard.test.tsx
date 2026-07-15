import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AppCard } from "./AppCard";
import { makeApp } from "../test/fixtures";

describe("AppCard", () => {
  it("renders the favorite control as an accessible SVG toggle", () => {
    render(<AppCard app={makeApp({ is_favorite: true })} />);
    const button = screen.getByRole("button", { name: /remove .* favorites/i });
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(button.querySelector("svg")).not.toBeNull();
  });
  it("links to the application URL and opens safely in a new tab", () => {
    render(<AppCard app={makeApp()} />);

    const link = screen.getByRole("link", { name: /Hunt Workbench/ });
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

  it("shows publisher team instead of shared team-scope badges", () => {
    render(
      <AppCard
        app={makeApp({ teams: ["Threat Hunting"], publisher_team: "Red Team" })}
      />,
    );
    expect(screen.getByText("Team: Red Team")).toBeInTheDocument();
    expect(screen.queryByText("Threat Hunting")).toBeNull();
  });

  it("shows the publisher local-part", () => {
    render(<AppCard app={makeApp({ created_by: "publisher@example.com" })} />);
    expect(screen.getByText(/published by: publisher/i)).toBeInTheDocument();
  });

  it("shows a separate edit link when provided", () => {
    render(<AppCard app={makeApp()} editHref="/settings?editApp=1" />);
    const edit = screen.getByRole("link", { name: /^edit$/i });
    expect(edit).toHaveAttribute("href", "/settings?editApp=1");
    expect(screen.getByRole("link", { name: /Hunt Workbench/ })).toHaveAttribute(
      "target",
      "_blank",
    );
  });
});
