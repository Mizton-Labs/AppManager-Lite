import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ServerProvisioning } from "./ServerProvisioning";
import type { ProvisioningSettings } from "../types";

const defaults: ProvisioningSettings = {
  provider_type: "",
  proxmox_url: "",
  proxmox_token_name: "",
  proxmox_api_key_set: false,
  proxmox_template_filter: "",
  proxmox_templates_only: true,
  proxmox_verify_tls: true,
  proxmox_conn_status: "",
  proxmox_conn_log: "",
  provisioning_self_service: false,
  provisioning_max_servers: 3,
  provisioning_allow_resource_edit: false,
  provisioning_max_cpus: 12,
  provisioning_max_memory_gb: 24,
  provisioning_max_disk_gb: 200,
  jump_enabled: false,
  jump_host: "",
  jump_user: "",
  jump_ssh_key_id: null,
};

function stubProvisioning(initial: Partial<ProvisioningSettings> = {}) {
  let store: ProvisioningSettings = { ...defaults, ...initial };
  const templates = [
    { vmid: 9001, name: "tpl-debian-coder", kind: "lxc", node: "pve1", is_template: true },
  ];
  let serverTemplates: unknown[] = [];
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const json = (payload: unknown, status = 200) =>
      ({ ok: status < 400, status, json: async () => payload }) as Response;

    if (url.endsWith("/api/settings/provisioning") && method === "GET") {
      return json(store);
    }
    if (url.endsWith("/api/settings/provisioning") && method === "PATCH") {
      const body = JSON.parse(init?.body as string);
      const { proxmox_api_key, ...rest } = body;
      store = {
        ...store,
        ...rest,
        ...(proxmox_api_key ? { proxmox_api_key_set: true } : {}),
      };
      if (
        "proxmox_url" in body ||
        "proxmox_api_key" in body ||
        "proxmox_token_name" in body
      ) {
        store.proxmox_conn_status = "ok";
        store.proxmox_conn_log = "[00:00:00] Connected: Proxmox VE version 8.2.4";
      }
      return json(store);
    }
    if (url.endsWith("/api/settings/provisioning/provider-templates")) {
      return json({ status: "ok", log: "", templates });
    }
    if (url.endsWith("/api/settings/ssh-keys")) {
      return json([
        { id: 3, name: "admin key", kind: "path", path: "/k",
          public_key: "", fingerprint: "", has_private_key: false },
      ]);
    }
    if (url.endsWith("/api/settings/jump-server/sync")) {
      return json({
        results: [
          { username: "a@example.com", status: "onboarded", detail: "" },
        ],
      });
    }
    if (url.endsWith("/api/settings/server-templates") && method === "GET") {
      return json(serverTemplates);
    }
    if (url.endsWith("/api/settings/server-templates") && method === "POST") {
      const body = JSON.parse(init?.body as string);
      serverTemplates = [{ id: 1, ...body }];
      return json(serverTemplates[0], 201);
    }
    if (url.includes("/api/settings/server-templates/") && method === "DELETE") {
      serverTemplates = [];
      return json({ detail: "Server template deleted" });
    }
    return json({ detail: `unexpected ${method} ${url}` }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ServerProvisioning", () => {
  it("saves the provider, shows the test result, and lists found templates", async () => {
    const fetchMock = stubProvisioning();
    render(<ServerProvisioning />);

    await screen.findByRole("heading", { name: /lxc\/vm provider/i });
    await userEvent.type(
      screen.getByLabelText(/proxmox url/i),
      "https://pve.example.com:8006",
    );
    await userEvent.type(screen.getByLabelText(/token name/i), "svc@pam!app");
    await userEvent.type(screen.getByLabelText(/^api key$/i), "topsecret");
    await userEvent.type(screen.getByLabelText(/template name filter/i), "tpl-");
    await userEvent.click(
      screen.getByRole("button", { name: /save and test connection/i }),
    );

    expect(
      await screen.findByText(/connection test succeeded/i),
    ).toBeInTheDocument();

    const patchCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/api/settings/provisioning") &&
        (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toMatchObject({
      provider_type: "proxmox",
      proxmox_url: "https://pve.example.com:8006",
      proxmox_token_name: "svc@pam!app",
      proxmox_api_key: "topsecret",
      proxmox_template_filter: "tpl-",
    });

    // Connection log is behind a toggle.
    await userEvent.click(
      screen.getByRole("button", { name: /view connection log/i }),
    );
    expect(screen.getByText(/proxmox ve version 8\.2\.4/i)).toBeInTheDocument();

    // The verification dropdown lists provider templates.
    expect(
      await screen.findByRole("option", { name: /tpl-debian-coder/i }),
    ).toBeInTheDocument();
  });

  it("does not send the api key when the field is left empty", async () => {
    const fetchMock = stubProvisioning({
      provider_type: "proxmox",
      proxmox_url: "https://pve:8006",
      proxmox_token_name: "svc@pam!app",
      proxmox_api_key_set: true,
    });
    render(<ServerProvisioning />);

    await screen.findByRole("heading", { name: /lxc\/vm provider/i });
    expect(
      screen.getByPlaceholderText(/unchanged - enter a new key/i),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /save and test connection/i }),
    );

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/api/settings/provisioning") &&
          (init as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
      const body = JSON.parse((patchCall![1] as RequestInit).body as string);
      expect(body).not.toHaveProperty("proxmox_api_key");
    });
  });

  it("saves the provisioning policy", async () => {
    const fetchMock = stubProvisioning();
    render(<ServerProvisioning />);

    await screen.findByRole("heading", { name: /server provisioning policy/i });
    await userEvent.click(
      screen.getByLabelText(/enable self-service server provisioning/i),
    );
    const maxServers = screen.getByLabelText(/max servers per user/i);
    await userEvent.clear(maxServers);
    await userEvent.type(maxServers, "5");
    await userEvent.click(screen.getByRole("button", { name: /save policy/i }));

    expect(await screen.findByText(/policy saved/i)).toBeInTheDocument();
    const patchCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith("/api/settings/provisioning") &&
        (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(JSON.parse((patchCall![1] as RequestInit).body as string)).toMatchObject({
      provisioning_self_service: true,
      provisioning_max_servers: 5,
      provisioning_max_cpus: 12,
      provisioning_max_memory_gb: 24,
      provisioning_max_disk_gb: 200,
    });
  });

  it("adds and deletes a server template", async () => {
    stubProvisioning();
    render(<ServerProvisioning />);

    await screen.findByRole("heading", { name: /server templates/i });
    await userEvent.type(screen.getByLabelText(/lxc\/vm id/i), "9001");
    await userEvent.type(screen.getByLabelText(/^template name$/i), "Debian Coder");
    await userEvent.click(screen.getByLabelText(/existing vm template/i));
    await userEvent.selectOptions(
      await screen.findByLabelText(/admin ssh key/i),
      "3",
    );
    await userEvent.click(screen.getByRole("button", { name: /add template/i }));

    expect(await screen.findByText("Debian Coder")).toBeInTheDocument();
    expect(screen.getByText("VM")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /delete/i }));
    expect(
      await screen.findByText(/no server templates registered yet/i),
    ).toBeInTheDocument();
  });

  it("warns when TLS verification is disabled", async () => {
    stubProvisioning();
    render(<ServerProvisioning />);

    await screen.findByRole("heading", { name: /lxc\/vm provider/i });
    await userEvent.click(screen.getByLabelText(/verify tls certificate/i));
    expect(
      screen.getByText(/vulnerable to interception/i),
    ).toBeInTheDocument();
  });

  it("saves jump server settings and syncs users", async () => {
    const fetchMock = stubProvisioning({
      jump_enabled: true,
      jump_host: "10.0.0.9",
      jump_user: "root",
      jump_ssh_key_id: 3,
    });
    render(<ServerProvisioning />);

    await screen.findByRole("heading", { name: /jump server/i });
    await userEvent.click(
      screen.getByRole("button", { name: /sync users to jump server/i }),
    );
    expect(await screen.findByText(/sync summary/i)).toBeInTheDocument();
    expect(screen.getByText("a@example.com")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([u]) =>
        String(u).endsWith("/api/settings/jump-server/sync"),
      ),
    ).toBe(true);
  });
});
