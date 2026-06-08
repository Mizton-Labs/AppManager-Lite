import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AboutView } from "./AboutView";
import { getAppName, GITHUB_URL } from "../branding";

describe("AboutView", () => {
  it("shows the application name", () => {
    render(<AboutView />);
    expect(screen.getByText(getAppName())).toBeInTheDocument();
  });

  it("links to the GitHub repository", () => {
    render(<AboutView />);
    const link = screen.getByRole("link", { name: /github/i });
    expect(link).toHaveAttribute("href", GITHUB_URL);
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("shows the version with the commit baked in", () => {
    render(<AboutView />);
    // Rendered as "<version> (<commit>)".
    expect(screen.getByText(/\(.+\)/)).toBeInTheDocument();
  });

  it("lists the development team with Javier Santillan first and Eduardo Duarte present", () => {
    render(<AboutView />);
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items.length).toBeGreaterThan(0);
    expect(items[0]).toBe("Javier Santillan");
    expect(items).toContain("Eduardo Duarte");
  });
});
