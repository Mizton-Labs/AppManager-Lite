import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveIconSrc, resolveAppHref } from "./links";

/**
 * Override the page protocol for a single assertion. jsdom's real
 * `location.protocol` is non-configurable, so we stub the whole `location`
 * global (which mirrors `window.location` / `globalThis.location`) with a
 * minimal shape exposing just `protocol`, then restore it afterwards.
 */
function withProtocol(protocol: "http:" | "https:", fn: () => void): void {
  vi.stubGlobal("location", { protocol });
  try {
    fn();
  } finally {
    vi.unstubAllGlobals();
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("resolveIconSrc", () => {
  it("returns an empty string for an empty value", () => {
    expect(resolveIconSrc("")).toBe("");
  });

  it("upgrades an absolute http: icon to https: on an https page", () => {
    withProtocol("https:", () => {
      expect(resolveIconSrc("http://cdn.example.com/i.png")).toBe(
        "https://cdn.example.com/i.png",
      );
    });
  });

  it("leaves an http: icon unchanged on an http page", () => {
    withProtocol("http:", () => {
      expect(resolveIconSrc("http://cdn.example.com/i.png")).toBe(
        "http://cdn.example.com/i.png",
      );
    });
  });

  it("passes https: icons through unchanged", () => {
    withProtocol("https:", () => {
      expect(resolveIconSrc("https://cdn.example.com/i.png")).toBe(
        "https://cdn.example.com/i.png",
      );
    });
  });

  it("passes data: URIs through unchanged even on https pages", () => {
    withProtocol("https:", () => {
      expect(resolveIconSrc("data:image/png;base64,AAAA")).toBe(
        "data:image/png;base64,AAAA",
      );
    });
  });

  it("does not touch the http substring inside a data: URI", () => {
    withProtocol("https:", () => {
      const uri = "data:text/plain,http://not-a-scheme";
      expect(resolveIconSrc(uri)).toBe(uri);
    });
  });

  it("upgrades an uppercase HTTP:// scheme on an https page", () => {
    withProtocol("https:", () => {
      expect(resolveIconSrc("HTTP://cdn.example.com/i.png")).toBe(
        "https://cdn.example.com/i.png",
      );
    });
  });

  it("resolves a protocol-relative //host reference against the base URI", () => {
    withProtocol("https:", () => {
      // No explicit scheme: routed to URL resolution (inheriting the real page
      // scheme in the browser), never the http->https string rewrite branch.
      const resolved = resolveIconSrc("//cdn.example.com/i.png");
      expect(resolved.endsWith("//cdn.example.com/i.png")).toBe(true);
    });
  });

  it("leaves an http: value without an authority unchanged", () => {
    withProtocol("https:", () => {
      // Not an absolute http:// URL, so it is not a mixed-content resource.
      expect(resolveIconSrc("http:relative")).toBe("http:relative");
    });
  });

  it("resolves a bundled relative logo path against the document base URI", () => {
    const resolved = resolveIconSrc("logos/red-team-2.svg");
    // Resolves against document.baseURI (or falls back to the input value if
    // the base is unavailable); either way it references the same bundled path.
    expect(resolved.endsWith("logos/red-team-2.svg")).toBe(true);
  });
});

describe("resolveAppHref", () => {
  it("returns a full-url application verbatim", () => {
    expect(
      resolveAppHref({ url: "https://example.com/app", url_type: "url" }),
    ).toBe("https://example.com/app");
  });

  it("resolves an alias application against the document base URI", () => {
    const resolved = resolveAppHref({ url: "pve2/", url_type: "alias" });
    expect(resolved.endsWith("pve2/")).toBe(true);
  });
});
