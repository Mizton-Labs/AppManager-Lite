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
          element={<EmbeddedAppView />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("EmbeddedAppView", () => {
  it("renders a title bar and an iframe pointing at the same-origin alias path", async () => {
    stubApps([
      makeApp({
        id: 7,
        name: "Coder Embed",
        // Embedded apps store the alias slug; the iframe resolves it to the
        // same-origin alias path served by the reverse proxy.
        url: "coder-app",
        url_type: "embedded",
      }),
    ]);
    const { container } = renderAt(7);

    expect(await screen.findByText("Coder Embed")).toBeInTheDocument();
    const frame = container.querySelector("iframe.embedded-frame");
    expect(frame).not.toBeNull();
    // Resolved against the document base (same origin as the portal).
    const src = frame?.getAttribute("src") ?? "";
    expect(src).toBe(new URL("coder-app", document.baseURI).toString());
    expect(new URL(src).origin).toBe(window.location.origin);
    expect(frame?.getAttribute("title")).toBe("Coder Embed");
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
