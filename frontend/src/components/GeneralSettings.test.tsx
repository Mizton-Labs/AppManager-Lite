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
  alias_template: "location /ALIAS/ { proxy_pass http://APPS_SERVER:APPS_PORT/; }",
};

afterEach(() => vi.unstubAllGlobals());

describe("GeneralSettings", () => {
  it("loads and shows the reverse-proxy fields", async () => {
    stubSettings(SAMPLE);
    render(<GeneralSettings />);

    expect(
      await screen.findByDisplayValue("proxy.example.com"),
    ).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("/data/keys/proxy_ed25519"),
    ).toBeInTheDocument();
    // The SSH user field is shown and loaded.
    expect(screen.getByDisplayValue("deploy")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(
      "Alias authentication requirement",
    );
    expect(screen.getByText(/location = \/api\/auth\/proxy-check/)).toBeInTheDocument();
  });

  it("keeps the alias template collapsed by default", async () => {
    stubSettings(SAMPLE);
    render(<GeneralSettings />);

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
    });
  });

  it("adds, removes, and saves collaborators", async () => {
    const fetchMock = stubSettings(SAMPLE, {
      app_name: "",
      app_logo: "",
      collaborators: ["Existing Person"],
      configured: false,
    });
    render(<GeneralSettings />);

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
});
