import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GeneralSettings } from "./GeneralSettings";
import type { BrandingSettings, ReverseProxySettings } from "../types";

/**
 * Stub the settings endpoints used by GeneralSettings' three cards:
 * - GET/PATCH /api/settings/branding (app name/logo + collaborators)
 * - GET/PATCH /api/settings/reverse-proxy
 */
function stubSettings(
  proxy: ReverseProxySettings,
  branding: BrandingSettings = {
    app_name: "",
    app_logo: "",
    collaborators: [],
    default_theme: "dark-modern",
    configured: false,
  },
) {
  let proxyStore = { ...proxy };
  let brandingStore = { ...branding };
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (/\/api\/settings\/branding\b/.test(url)) {
      if (method === "PATCH") brandingStore = { ...brandingStore, ...body };
      return { ok: true, status: 200, json: async () => brandingStore } as Response;
    }
    if (/\/api\/settings\/ssh-keys\b/.test(url)) {
      return {
        ok: true,
        status: 200,
        json: async () => [
          {
            id: 5,
            name: "proxy key",
            kind: "path",
            path: "/data/keys/proxy_ed25519",
            public_key: "",
            fingerprint: "",
            has_private_key: false,
          },
        ],
      } as Response;
    }
    // Default: reverse-proxy.
    if (method === "PATCH") proxyStore = { ...proxyStore, ...body };
    return { ok: true, status: 200, json: async () => proxyStore } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const SAMPLE: ReverseProxySettings = {
  nginx_host: "proxy.example.com",
  nginx_user: "deploy",
  nginx_conf_path: "/etc/nginx/conf.d/apps.conf",
  ssh_key_path: "/data/keys/proxy_ed25519",
  reverse_proxy_ssh_key_id: 5,
  appmanager_proxy_host: "appmanager",
  appmanager_proxy_port: "8000",
  alias_template: "location /ALIAS/ { proxy_pass http://APPS_SERVER:APPS_PORT/; }",
  protected_alias_auth_status: "",
  protected_alias_auth_log: "",
};

afterEach(() => vi.unstubAllGlobals());

/** Click a Settings sub-tab by its visible label. */
async function openTab(label: RegExp) {
  await userEvent.click(await screen.findByRole("button", { name: label }));
}

describe("GeneralSettings", () => {
  it("loads and shows the reverse-proxy fields", async () => {
    stubSettings(SAMPLE);
    render(<GeneralSettings />);
    await openTab(/Reverse Proxy/i);

    expect(
      await screen.findByDisplayValue("proxy.example.com"),
    ).toBeInTheDocument();
    // The SSH key is now selected from the registry dropdown by name.
    expect(await screen.findByRole("option", { name: /proxy key/i })).toBeInTheDocument();
    // The SSH user field is shown and loaded.
    expect(screen.getByDisplayValue("deploy")).toBeInTheDocument();
    expect(screen.getByDisplayValue("appmanager")).toBeInTheDocument();
    expect(screen.getByDisplayValue("8000")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(
      "Alias authentication requirement",
    );
    expect(screen.getByText(/location = \/api\/auth\/proxy-check/)).toBeInTheDocument();
  });

  it("lands on the Reverse Proxy sub-tab on first run", async () => {
    stubSettings(SAMPLE);
    render(<GeneralSettings firstRun />);

    // Reverse-proxy setup fields are visible without any extra clicks.
    expect(
      await screen.findByDisplayValue("proxy.example.com"),
    ).toBeInTheDocument();
  });

  it("keeps the alias template collapsed by default", async () => {
    stubSettings(SAMPLE);
    render(<GeneralSettings />);
    await openTab(/Reverse Proxy/i);

    await screen.findByDisplayValue("proxy.example.com");
    // The template textarea is not rendered until expanded.
    expect(screen.queryByLabelText(/alias template/i)).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /show alias template/i }),
    );
    expect(screen.getByLabelText(/alias template/i)).toBeInTheDocument();
  });

  it("saves updated settings including the SSH user", async () => {
    const fetchMock = stubSettings(SAMPLE);
    render(<GeneralSettings />);
    await openTab(/Reverse Proxy/i);

    const hostInput = await screen.findByDisplayValue("proxy.example.com");
    await userEvent.clear(hostInput);
    await userEvent.type(hostInput, "new-proxy.example.com");
    const userInput = screen.getByDisplayValue("deploy");
    await userEvent.clear(userInput);
    await userEvent.type(userInput, "ubuntu");
    await userEvent.click(screen.getByRole("button", { name: /save settings/i }));

    expect(await screen.findByText(/settings saved/i)).toBeInTheDocument();
    const patch = fetchMock.mock.calls.find(
      ([url, init]) =>
        /reverse-proxy/.test(String(url)) &&
        (init?.method ?? "GET") === "PATCH",
    );
    expect(JSON.parse((patch![1] as RequestInit).body as string)).toMatchObject({
      nginx_host: "new-proxy.example.com",
      nginx_user: "ubuntu",
      appmanager_proxy_host: "appmanager",
      appmanager_proxy_port: "8000",
    });
  });

  it("adds, removes, and saves collaborators", async () => {
    const fetchMock = stubSettings(SAMPLE, {
      app_name: "",
      app_logo: "",
      collaborators: ["Existing Person"],
      default_theme: "dark-modern",
      configured: false,
    });
    render(<GeneralSettings />);
    await openTab(/Collaborators/i);

    // The existing collaborator loads.
    const list = await screen.findByRole("list", { name: "Collaborators" });
    expect(within(list).getByText("Existing Person")).toBeInTheDocument();

    // Add a new collaborator.
    await userEvent.type(
      screen.getByRole("textbox", { name: /collaborator name/i }),
      "New Person",
    );
    await userEvent.click(screen.getByRole("button", { name: /^Add$/ }));
    expect(within(list).getByText("New Person")).toBeInTheDocument();

    // Remove the original.
    await userEvent.click(
      screen.getByRole("button", { name: /remove existing person/i }),
    );
    expect(within(list).queryByText("Existing Person")).toBeNull();

    // Save sends only the collaborators array.
    await userEvent.click(
      screen.getByRole("button", { name: /save collaborators/i }),
    );
    expect(await screen.findByText(/collaborators saved/i)).toBeInTheDocument();
    const patch = fetchMock.mock.calls.find(
      ([url, init]) =>
        /branding/.test(String(url)) && (init?.method ?? "GET") === "PATCH",
    );
    const sent = JSON.parse((patch![1] as RequestInit).body as string);
    expect(sent).toEqual({ collaborators: ["New Person"] });
  });

  it("saves the admin default theme (issue_019)", async () => {
    const fetchMock = stubSettings(SAMPLE);
    render(<GeneralSettings />);
    await openTab(/Basic Information/i);

    await userEvent.selectOptions(
      await screen.findByLabelText("Default theme"),
      "energy",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /save basic information/i }),
    );
    const patch = fetchMock.mock.calls.find(
      ([url, init]) =>
        /branding/.test(String(url)) && (init?.method ?? "GET") === "PATCH",
    );
    expect(patch).toBeTruthy();
    const sent = JSON.parse((patch![1] as RequestInit).body as string);
    expect(sent.default_theme).toBe("energy");
  });
});
