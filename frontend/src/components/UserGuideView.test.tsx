import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { UserGuideView } from "./UserGuideView";

describe("UserGuideView", () => {
  it("renders the main guide sections", () => {
    render(<UserGuideView />);
    expect(
      screen.getByRole("heading", { name: /user guide/i, level: 1 }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /getting started/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /launching applications/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /your servers/i }),
    ).toBeInTheDocument();
  });

  it("includes a clearly labeled administrators section with the push guidance", () => {
    render(<UserGuideView />);
    expect(
      screen.getByRole("heading", { name: /for administrators/i }),
    ).toBeInTheDocument();
    // The push-to-reverse-proxy guidance is called out for admins.
    expect(screen.getByText(/push to reverse proxy/i)).toBeInTheDocument();
    // Diagrams render as labeled figures (role="img").
    expect(screen.getAllByRole("img").length).toBeGreaterThan(0);
  });
});
