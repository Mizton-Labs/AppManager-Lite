import { afterEach, describe, expect, it } from "vitest";
import { DEFAULT_APP_NAME, setBranding } from "./branding";

afterEach(() => {
  setBranding({ app_name: "", app_logo: "", collaborators: [] });
});

describe("branding", () => {
  it("sets the document title from the configured application name", () => {
    setBranding({ app_name: "Mizton Portal", app_logo: "", collaborators: [] });
    expect(document.title).toBe("Mizton Portal");
  });

  it("falls back to the default document title", () => {
    setBranding({ app_name: "", app_logo: "", collaborators: [] });
    expect(document.title).toBe(DEFAULT_APP_NAME);
  });
});
