import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SubTabs } from "./SubTabs";

afterEach(() => vi.restoreAllMocks());

describe("SubTabs", () => {
  it("shows the first tab by default and switches on click", async () => {
    render(
      <SubTabs
        ariaLabel="Test sections"
        tabs={[
          { id: "a", label: "Alpha", render: () => <p>Alpha body</p> },
          { id: "b", label: "Beta", render: () => <p>Beta body</p> },
        ]}
      />,
    );

    // First tab active + rendered; second hidden.
    expect(screen.getByText("Alpha body")).toBeInTheDocument();
    expect(screen.queryByText("Beta body")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Alpha" }),
    ).toHaveAttribute("aria-current", "true");

    await userEvent.click(screen.getByRole("button", { name: "Beta" }));
    expect(screen.getByText("Beta body")).toBeInTheDocument();
    expect(screen.queryByText("Alpha body")).toBeNull();
  });

  it("honors initialTab", () => {
    render(
      <SubTabs
        ariaLabel="Test sections"
        initialTab="b"
        tabs={[
          { id: "a", label: "Alpha", render: () => <p>Alpha body</p> },
          { id: "b", label: "Beta", render: () => <p>Beta body</p> },
        ]}
      />,
    );
    expect(screen.getByText("Beta body")).toBeInTheDocument();
    expect(screen.queryByText("Alpha body")).toBeNull();
  });
});
