import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GeneralSettings } from "./GeneralSettings";
import type { ReverseProxySettings } from "../types";

function stubSettings(initial: ReverseProxySettings) {
  let store = { ...initial };
  const fetchMock = vi.fn(async (_input: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    if (method === "PATCH") {
      store = { ...store, ...JSON.parse(init!.body as string) };
    }
    return { ok: true, status: 200, json: async () => store } as Response;
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
      ([, init]) => (init?.method ?? "GET") === "PATCH",
    );
    expect(JSON.parse((patch![1] as RequestInit).body as string)).toMatchObject({
      nginx_host: "new-proxy.example.com",
      nginx_user: "ubuntu",
    });
  });
});
