import { afterEach, describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { AboutView } from "./AboutView";
import { getAppName, GITHUB_URL, setBranding } from "../branding";

afterEach(() => {
  // Reset the in-memory branding store between tests.
  setBranding({ app_name: "", app_logo: "", collaborators: [] });
});

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

  it("lists the development team from the injected git contributors", () => {
    render(<AboutView />);
    // __APP_CONTRIBUTORS__ is injected by Vite at build time from git authors.
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items.length).toBeGreaterThan(0);
  });

  it("does not show a Collaborators section when none are configured", () => {
    setBranding({ collaborators: [] });
    render(<AboutView />);
    expect(screen.queryByText("Collaborators")).toBeNull();
  });

  it("shows admin-configured collaborators below the development team", () => {
    setBranding({ collaborators: ["Jane Doe", "John Smith"] });
    render(<AboutView />);

    const dev = screen.getByText("Development team").closest(".detail-row");
    const collab = screen.getByText("Collaborators").closest(".detail-row");
    expect(dev).not.toBeNull();
    expect(collab).not.toBeNull();
    // Collaborators row appears after the development-team row in the DOM.
    expect(
      dev!.compareDocumentPosition(collab!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(within(collab as HTMLElement).getByText("Jane Doe")).toBeInTheDocument();
    expect(
      within(collab as HTMLElement).getByText("John Smith"),
    ).toBeInTheDocument();
  });
});
