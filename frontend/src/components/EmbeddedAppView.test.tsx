import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { EmbeddedAppView } from "./EmbeddedAppView";
import { makeApp } from "../test/fixtures";

function stubApps(apps: unknown[]): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const url = String(input);
      if (/\/api\/applications(\?|$)/.test(url)) {
        return { ok: true, status: 200, json: async () => apps } as Response;
      }
      return { ok: true, status: 200, json: async () => [] } as Response;
    }),
  );
}

function renderAt(id: number) {
  return render(
    <MemoryRouter initialEntries={[`/embedded/${id}`]}>
      <Routes>
        <Route
          path="/embedded/:id"
          element={<EmbeddedAppView collapsed={false} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("EmbeddedAppView", () => {
  it("renders a title bar and an iframe pointing at the app source", async () => {
    stubApps([
      makeApp({
        id: 7,
        name: "Grafana Embed",
        url: "http://10.0.0.5:3000/",
        url_type: "embedded",
      }),
    ]);
    const { container } = renderAt(7);

    expect(await screen.findByText("Grafana Embed")).toBeInTheDocument();
    const frame = container.querySelector("iframe.embedded-frame");
    expect(frame).not.toBeNull();
    expect(frame?.getAttribute("src")).toBe("http://10.0.0.5:3000/");
    expect(frame?.getAttribute("title")).toBe("Grafana Embed");
    expect(frame?.getAttribute("sandbox")).toContain("allow-scripts");
  });

  it("shows an error when the embedded app is not accessible", async () => {
    stubApps([makeApp({ id: 1, url_type: "url" })]);
    renderAt(99);
    expect(
      await screen.findByText(/not available/i),
    ).toBeInTheDocument();
  });
});
