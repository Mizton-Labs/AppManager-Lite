import { describe, expect, it } from "vitest";
import { defaultLogoFor, stableHash } from "./logos";
import { teamSlug } from "./teams";

describe("defaultLogoFor", () => {
  it("returns a stable, relative logo path for the same app", () => {
    const a = defaultLogoFor("Hunt Workbench", ["Threat Hunting"]);
    const b = defaultLogoFor("Hunt Workbench", ["Threat Hunting"]);
    expect(a).toBe(b);
    expect(a).toMatch(/^logos\/threat-hunting-[1-3]\.svg$/);
  });

  it("uses the first team in canonical order when several are present", () => {
    // "Threat Intel" precedes "Red Team" in ALL_TEAMS, so its set is chosen
    // regardless of the order the teams are listed on the app.
    const url = defaultLogoFor("Some App", ["Red Team", "Threat Intel"]);
    expect(url).toBe(`logos/${teamSlug("Threat Intel")}-${url.slice(-5, -4)}.svg`);
    expect(url).toContain(`logos/${teamSlug("Threat Intel")}-`);
  });

  it("falls back to the generic set when the app has no team", () => {
    const url = defaultLogoFor("No Team App", []);
    expect(url).toMatch(/^logos\/generic-[1-3]\.svg$/);
  });

  it("ignores unknown teams and uses the generic set", () => {
    const url = defaultLogoFor("Mystery", ["Not A Real Team"]);
    expect(url).toMatch(/^logos\/generic-[1-3]\.svg$/);
  });

  it("spreads variants across the 1..3 range by app name", () => {
    const names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"];
    const variants = new Set(
      names.map((name) => {
        const url = defaultLogoFor(name, ["Red Team"]);
        return url.slice(-5, -4); // the digit before ".svg"
      }),
    );
    // At least two distinct variants appear, proving the pick is name-driven.
    expect(variants.size).toBeGreaterThan(1);
    for (const v of variants) {
      expect(["1", "2", "3"]).toContain(v);
    }
  });
});

describe("stableHash", () => {
  it("is deterministic and unsigned", () => {
    expect(stableHash("abc")).toBe(stableHash("abc"));
    expect(stableHash("abc")).toBeGreaterThanOrEqual(0);
  });

  it("differs for different inputs", () => {
    expect(stableHash("abc")).not.toBe(stableHash("abd"));
  });
});
