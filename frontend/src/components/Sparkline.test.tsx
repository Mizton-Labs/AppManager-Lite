import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Sparkline, sparklinePath } from "./Sparkline";

describe("sparklinePath", () => {
  it("returns empty paths for no values", () => {
    expect(sparklinePath([], 100, 40, 100)).toEqual({ line: "", area: "" });
  });

  it("maps values to an inverted Y within the viewBox", () => {
    // max=100, height=40: value 100 -> y=0 (top), value 0 -> y=40 (bottom).
    const { line } = sparklinePath([0, 100], 100, 40, 100);
    // Two points: x=0,y=40 then x=100,y=0.
    expect(line).toBe("M0.0,40.0 L100.0,0.0");
  });

  it("scales to the series max when max is 0", () => {
    const { line } = sparklinePath([5, 10], 100, 40, 0);
    // series max = 10 -> value 5 => y=20, value 10 => y=0.
    expect(line).toBe("M0.0,20.0 L100.0,0.0");
  });

  it("clamps values above max to the top", () => {
    const { line } = sparklinePath([200], 100, 40, 100);
    // single point centered; clamped to top (y=0).
    expect(line).toBe("M50.0,0.0");
  });

  it("builds a closed area path anchored to the baseline", () => {
    const { area } = sparklinePath([50], 100, 40, 100);
    expect(area.startsWith("M0,40.0")).toBe(true);
    expect(area.trimEnd().endsWith("Z")).toBe(true);
  });
});

describe("Sparkline", () => {
  it("renders the label and current value", () => {
    render(
      <Sparkline label="CPU" values={[10, 20]} max={100} valueLabel="20%" />,
    );
    expect(screen.getByText("CPU")).toBeInTheDocument();
    expect(screen.getByText("20%")).toBeInTheDocument();
    // Accessible group labelled by the metric name.
    expect(screen.getByRole("group", { name: "CPU" })).toBeInTheDocument();
  });

  it("applies the tone class to the line", () => {
    const { container } = render(
      <Sparkline label="Mem" values={[1, 2]} valueLabel="x" tone="full" />,
    );
    expect(container.querySelector(".spark-line.spark-full")).not.toBeNull();
  });
});
