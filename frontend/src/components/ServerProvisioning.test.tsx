import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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
  jump_port: 22,
  jump_ssh_key_id: null,
  jump_management_user: "root",
  jump_account_mode: "per_user",
  jump_jumper_user: "",
  jump_bundle_override: false,
  jump_bundle_host: "",
  jump_bundle_port: 22,
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
    if (url.endsWith("/api/settings/jump-server/account-mode")) {
      const body = JSON.parse(init?.body as string);
      // Simulate a failing sync (revert) when the special marker name is used.
      if (body.jumper_user === "FAIL") {
        return json({
          account_mode: "per_user",
          reverted: true,
          detail: "Re-sync failed; reverted. a@example.com: boom",
          results: [
            { username: "a@example.com", status: "failed", detail: "boom" },
          ],
        });
      }
      store = {
        ...store,
        jump_account_mode: body.account_mode,
        jump_jumper_user: body.jumper_user ?? store.jump_jumper_user,
      };
      return json({
        account_mode: body.account_mode,
        reverted: false,
        detail: "",
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

/** Click a Server Provisioning sub-tab by its visible label. */
async function openTab(label: RegExp) {
  await userEvent.click(await screen.findByRole("button", { name: label }));
}

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
    await openTab(/^Policy$/i);

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
    await openTab(/Server Templates/i);

    await screen.findByRole("heading", { name: /^server templates$/i });
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
    // The list card shows the configured options as badges.
    expect(screen.getByText(/sudo: on/i)).toBeInTheDocument();
    expect(screen.getByText(/trusted ssh: on/i)).toBeInTheDocument();
    // The add card explains the admin key's purpose.
    expect(
      screen.getByText(/manage server-template operations/i),
    ).toBeInTheDocument();

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
      jump_port: 2222,
      jump_ssh_key_id: 3,
    });
    render(<ServerProvisioning />);
    await openTab(/Jump Server/i);

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

  it("reveals bundle address fields when the admin-config toggle is off", async () => {
    const fetchMock = stubProvisioning({
      jump_enabled: true,
      jump_host: "10.0.0.9",
      jump_user: "root",
      jump_port: 2222,
      jump_ssh_key_id: 3,
    });
    render(<ServerProvisioning />);
    await openTab(/Jump Server/i);
    await screen.findByRole("heading", { name: /jump server/i });

    const toggle = screen.getByLabelText(
      /use jumpserver admin config in ssh config bundle/i,
    );
    expect(toggle).toBeChecked();
    // Fields hidden while using the admin config.
    expect(screen.queryByLabelText(/bundle host/i)).toBeNull();

    await userEvent.click(toggle); // turn override ON
    await userEvent.type(
      screen.getByLabelText(/bundle host/i),
      "public.example.com",
    );
    const portField = screen.getByLabelText(/bundle ssh port/i);
    await userEvent.clear(portField);
    await userEvent.type(portField, "443");
    await userEvent.click(
      screen.getByRole("button", { name: /save jump server/i }),
    );

    const patch = fetchMock.mock.calls.find(
      ([u, init]) =>
        String(u).endsWith("/api/settings/provisioning") &&
        (init?.method ?? "GET") === "PATCH",
    );
    expect(JSON.parse((patch![1] as RequestInit).body as string)).toMatchObject({
      jump_bundle_override: true,
      jump_bundle_host: "public.example.com",
      jump_bundle_port: 443,
    });
    // Bundle-only change advises that no re-sync is needed for the same host.
    expect(
      await screen.findByText(/only affects the address written/i),
    ).toBeInTheDocument();
  });

  it("warns to re-sync users when a connection field changes", async () => {
    stubProvisioning({
      jump_enabled: true,
      jump_host: "10.0.0.9",
      jump_user: "root",
      jump_port: 2222,
      jump_ssh_key_id: 3,
    });
    render(<ServerProvisioning />);
    await openTab(/Jump Server/i);
    await screen.findByRole("heading", { name: /jump server/i });

    const hostField = screen.getByLabelText(/jump host/i);
    await userEvent.clear(hostField);
    await userEvent.type(hostField, "10.0.0.50");
    await userEvent.click(
      screen.getByRole("button", { name: /save jump server/i }),
    );

    expect(
      await screen.findByText(/existing users must be re-synced/i),
    ).toBeInTheDocument();
  });

  it("does not warn to re-sync when the jump server is disabled", async () => {
    stubProvisioning({
      jump_enabled: false,
      jump_host: "10.0.0.9",
      jump_user: "root",
      jump_port: 2222,
      jump_ssh_key_id: 3,
    });
    render(<ServerProvisioning />);
    await openTab(/Jump Server/i);
    await screen.findByRole("heading", { name: /jump server/i });

    const hostField = screen.getByLabelText(/jump host/i);
    await userEvent.clear(hostField);
    await userEvent.type(hostField, "10.0.0.50");
    await userEvent.click(
      screen.getByRole("button", { name: /save jump server/i }),
    );

    expect(await screen.findByText(/settings saved/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/existing users must be re-synced/i),
    ).toBeNull();
  });

  it("clears the sync reminder after syncing users", async () => {
    stubProvisioning({
      jump_enabled: true,
      jump_host: "10.0.0.9",
      jump_user: "root",
      jump_port: 2222,
      jump_ssh_key_id: 3,
    });
    render(<ServerProvisioning />);
    await openTab(/Jump Server/i);
    await screen.findByRole("heading", { name: /jump server/i });

    const hostField = screen.getByLabelText(/jump host/i);
    await userEvent.clear(hostField);
    await userEvent.type(hostField, "10.0.0.50");
    await userEvent.click(
      screen.getByRole("button", { name: /save jump server/i }),
    );
    expect(
      await screen.findByText(/existing users must be re-synced/i),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /sync users to jump server/i }),
    );
    await screen.findByText(/sync summary/i);
    expect(
      screen.queryByText(/existing users must be re-synced/i),
    ).toBeNull();
  });

  it("confirms and applies a jump account-model switch to shared", async () => {
    const fetchMock = stubProvisioning({
      jump_enabled: true,
      jump_host: "10.0.0.9",
      jump_management_user: "root",
      jump_account_mode: "per_user",
      jump_ssh_key_id: 3,
    });
    render(<ServerProvisioning />);
    await openTab(/Jump Server/i);
    await screen.findByRole("heading", { name: /jump server/i });

    // Selecting the shared radio opens the confirmation modal.
    await userEvent.click(
      screen.getByRole("radio", { name: /share one hardened account/i }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(
      within(dialog).getByLabelText(/shared jump account name/i),
      "cdt-jumper",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: /acknowledge & re-sync/i }),
    );

    const call = fetchMock.mock.calls.find(([u]) =>
      String(u).endsWith("/api/settings/jump-server/account-mode"),
    );
    expect(JSON.parse((call![1] as RequestInit).body as string)).toMatchObject({
      account_mode: "shared",
      jumper_user: "cdt-jumper",
      acknowledge_sync: true,
    });
    // The modal closes and the sync summary is shown.
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).toBeNull(),
    );
    expect(await screen.findByText(/sync summary/i)).toBeInTheDocument();
  });

  it("surfaces the error when an account-model switch is reverted", async () => {
    stubProvisioning({
      jump_enabled: true,
      jump_host: "10.0.0.9",
      jump_management_user: "root",
      jump_account_mode: "per_user",
      jump_ssh_key_id: 3,
    });
    render(<ServerProvisioning />);
    await openTab(/Jump Server/i);
    await screen.findByRole("heading", { name: /jump server/i });

    await userEvent.click(
      screen.getByRole("radio", { name: /share one hardened account/i }),
    );
    const dialog = await screen.findByRole("dialog");
    // The special "FAIL" name makes the stubbed sync fail and revert.
    await userEvent.type(
      within(dialog).getByLabelText(/shared jump account name/i),
      "FAIL",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: /acknowledge & re-sync/i }),
    );

    expect(await screen.findByText(/reverted/i)).toBeInTheDocument();
  });
});
