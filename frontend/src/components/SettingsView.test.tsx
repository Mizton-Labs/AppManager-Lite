import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SettingsView } from "./SettingsView";
import { makeUser } from "../test/fixtures";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** Generic stub: every request resolves with an empty/neutral payload so the
 * tab bar renders without unhandled promise rejections. This test only
 * exercises the settings tab bar itself, not any individual tab's content. */
function stubEverything() {
  const fetchMock = vi.fn(async (input: string) => {
    const url = String(input);
    if (url.includes("/api/settings/reverse-proxy")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          nginx_host: "",
          nginx_conf_path: "",
          ssh_key_id: null,
          appmanager_proxy_host: "",
          appmanager_proxy_port: "",
          alias_template: "",
        }),
      } as Response;
    }
    if (url.includes("/api/settings/branding")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          app_name: "",
          app_logo: "",
          collaborators: [],
          default_theme: "dark-modern",
          configured: false,
        }),
      } as Response;
    }
    return { ok: true, status: 200, json: async () => ([]) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("SettingsView tab order", () => {
  it("shows General Settings as the first tab, before User Management", () => {
    stubEverything();
    render(
      <SettingsView
        isAdmin
        currentUser={makeUser({ role: "admin" })}
        appTeamOptions={[]}
      />,
    );
    const tabs = screen.getAllByRole("button", {
      name: /general settings|user management|teams|server provisioning|remote access/i,
    });
    const labels = tabs.map((t) => t.textContent);
    expect(labels[0]).toBe("General Settings");
    expect(labels.indexOf("General Settings")).toBeLessThan(
      labels.indexOf("User Management"),
    );
  });

  it("still defaults to the General Settings tab being active", () => {
    stubEverything();
    render(
      <SettingsView
        isAdmin
        currentUser={makeUser({ role: "admin" })}
        appTeamOptions={[]}
      />,
    );
    const generalTab = screen.getByRole("button", { name: "General Settings" });
    expect(generalTab.className).toContain("active");
  });
});
