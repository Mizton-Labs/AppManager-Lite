import { describe, expect, it } from "vitest";
import { isPortalLandingPage, safeNextPath } from "./navigation";

describe("safeNextPath", () => {
  it("accepts a rooted path", () => {
    expect(safeNextPath("/latmov/")).toBe("/latmov/");
  });

  it("accepts a rooted path with a query string", () => {
    expect(safeNextPath("/latmov/dash?view=a")).toBe("/latmov/dash?view=a");
  });

  it("accepts the root path", () => {
    expect(safeNextPath("/")).toBe("/");
  });

  it("rejects an absolute URL", () => {
    expect(safeNextPath("https://evil.example/path")).toBe("");
  });

  it("rejects a protocol-relative URL", () => {
    expect(safeNextPath("//evil.example/path")).toBe("");
  });

  it("rejects a backslash-prefixed value", () => {
    expect(safeNextPath("/\\evil.example")).toBe("");
  });

  it("rejects embedded control characters", () => {
    expect(safeNextPath("/grafana\r\nSet-Cookie: x=1")).toBe("");
  });

  it("rejects a non-rooted relative path", () => {
    expect(safeNextPath("relative-without-slash")).toBe("");
  });

  it("rejects null/undefined/empty", () => {
    expect(safeNextPath(null)).toBe("");
    expect(safeNextPath(undefined)).toBe("");
    expect(safeNextPath("")).toBe("");
  });
});

describe("isPortalLandingPage", () => {
  it("fails closed (false) when no <base> tag is present", () => {
    document.querySelectorAll("base").forEach((el) => el.remove());
    expect(isPortalLandingPage()).toBe(false);
  });

  it("is true when the current path matches the injected <base href>", () => {
    const base = document.createElement("base");
    base.href = "/";
    document.head.appendChild(base);
    try {
      window.history.pushState({}, "", "/");
      expect(isPortalLandingPage()).toBe(true);
    } finally {
      base.remove();
    }
  });
});
