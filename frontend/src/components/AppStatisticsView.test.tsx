import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MultiLineChart } from "./AppStatisticsView";

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
