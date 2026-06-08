import { describe, expect, it } from "vitest";
import { isPasswordValid, passwordIssues } from "./password";

describe("passwordIssues", () => {
  it("accepts a compliant password", () => {
    expect(passwordIssues("Sufficient1Pass")).toEqual([]);
    expect(isPasswordValid("Sufficient1Pass")).toBe(true);
  });

  it("flags passwords that are too short", () => {
    expect(passwordIssues("Ab1")).toContain("Use at least 12 characters.");
  });

  it("requires lowercase, uppercase, and a digit", () => {
    expect(passwordIssues("ALLUPPERCASE1")).toContain("Add a lowercase letter.");
    expect(passwordIssues("alllowercase1")).toContain("Add an uppercase letter.");
    expect(passwordIssues("NoDigitsHerePresent")).toContain("Add a digit.");
  });

  it("reports an invalid password overall", () => {
    expect(isPasswordValid("short")).toBe(false);
  });
});
