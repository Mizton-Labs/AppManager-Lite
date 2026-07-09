import type {
  ApiUser,
  Application,
  AliasConfig,
  AuditEntry,
  BrandingSettings,
  BundleDownload,
  BundleOption,
  BundleTemplate,
  CreateBundleTemplateInput,
  CreateApplicationInput,
  CreateTeamInput,
  CreateUserInput,
  GeneratedPassword,
  ReverseProxySettings,
  CreateServerTemplateInput,
  CreateSshKeyInput,
  UpdateSshKeyInput,
  CreateUserServerInput,
  JumpAccountModeInput,
  JumpAccountModeResult,
  JumpSyncEntry,
  ProviderTemplates,
  ProvisioningSettings,
  ServerAccess,
  ServersOverview,
  ServerStats,
  ServerTemplate,
  ServerTemplateOption,
  ServerUsage,
  SshKey,
  SessionState,
  SshKeyInfo,
  SshKeyRegenerateResult,
  SsoConfig,
  UpdateProvisioningSettingsInput,
  UpdateServerTemplateInput,
  UpdateUserServerInput,
  UserServer,
  Team,
  UpdateBundleTemplateInput,
  UpdateApplicationInput,
  UpdateBrandingSettingsInput,
  UpdateReverseProxySettingsInput,
  UpdateTeamInput,
  UpdateUserInput,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/**
 * Resolve the API root. Relative to the document's base URI, which the backend
 * sets via an injected <base href> matching the deployment prefix. In local
 * development this resolves against the dev-server origin (proxied to :8000).
 */
export function apiBase(): string {
  return new URL("api/", document.baseURI).toString();
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (method !== "GET" && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  const response = await fetch(apiBase() + path, {
    method,
    headers,
    credentials: "same-origin",
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      (data && typeof data.detail === "string" && data.detail) ||
      `Request failed (${response.status})`;
    throw new ApiError(response.status, detail);
  }
  return data as T;
}

async function requestText(path: string): Promise<BundleDownload> {
  const response = await fetch(apiBase() + path, {
    method: "GET",
    headers: { Accept: "text/plain" },
    credentials: "same-origin",
  });
  const content = await response.text();
  if (!response.ok) {
    throw new ApiError(response.status, content || `Request failed (${response.status})`);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return { content, filename: match?.[1] ?? "bundle.txt" };
}

export const api = {
  getSession: () => request<SessionState>("session"),

  getSsoConfig: () => request<SsoConfig>("auth/sso/config"),

  login: (username: string, password: string) =>
    request<SessionState>("auth/login", {
      method: "POST",
      body: { username, password },
    }),

  logout: () => request<{ detail: string }>("auth/logout", { method: "POST" }),

  changeOwnPassword: (
    current_password: string,
    new_password: string,
    confirm_password: string,
  ) =>
    request<{ detail: string }>("account/password", {
      method: "POST",
      body: { current_password, new_password, confirm_password },
    }),

  listAccountBundles: () => request<BundleOption[]>("account/bundles"),

  downloadAccountBundle: (id: number) =>
    requestText(`account/bundles/${id}/download`),

  getAccountSshKey: () => request<SshKeyInfo>("account/ssh-key"),

  downloadAccountSshKey: (part: "private" | "public") =>
    requestText(`account/ssh-key/download?part=${part}`),

  regenerateAccountSshKey: () =>
    request<SshKeyRegenerateResult>("account/ssh-key/regenerate", {
      method: "POST",
    }),

  listTeams: () => request<Team[]>("teams"),

  listApplications: (team?: string) =>
    request<Application[]>(
      team
        ? `applications?team=${encodeURIComponent(team)}`
        : "applications",
    ),

  listApplicationsByPublisherTeam: (publisherTeam: string) =>
    request<Application[]>(
      `applications?publisher_team=${encodeURIComponent(publisherTeam)}`,
    ),

  listAllApplications: () =>
    request<Application[]>("applications?include_inactive=true"),

  /** Applications the current user created, in any approval state. */
  listMyApplications: () => request<Application[]>("applications/mine"),

  /** Every application with creator and status (administrators only). */
  listManagedApplications: () =>
    request<Application[]>("applications/manage"),

  /** Audit-log entries, optionally filtered by category (administrators only). */
  listAuditLog: (category?: string) =>
    request<AuditEntry[]>(
      category ? `audit?category=${encodeURIComponent(category)}` : "audit",
    ),

  createApplication: (input: CreateApplicationInput) =>
    request<Application>("applications", { method: "POST", body: input }),

  updateApplication: (id: number, input: UpdateApplicationInput) =>
    request<Application>(`applications/${id}`, {
      method: "PATCH",
      body: input,
    }),

  deleteApplication: (id: number) =>
    request<{ detail: string }>(`applications/${id}`, { method: "DELETE" }),

  /** Re-run the reverse-proxy alias push for an approved application (admin). */
  retryApplicationPush: (id: number) =>
    request<Application>(`applications/${id}/push-retry`, { method: "POST" }),

  /** Read the current deployed nginx alias config for one app. */
  getApplicationAliasConfig: (id: number) =>
    request<AliasConfig>(`applications/${id}/alias-config`),

  listUsers: () => request<ApiUser[]>("users"),

  createUser: (input: CreateUserInput) =>
    request<GeneratedPassword>("users", { method: "POST", body: input }),

  updateUser: (id: number, input: UpdateUserInput) =>
    request<ApiUser>(`users/${id}`, { method: "PATCH", body: input }),

  resetPassword: (id: number) =>
    request<GeneratedPassword>(`users/${id}/reset-password`, { method: "POST" }),

  deleteUser: (id: number, options: { delete_apps?: boolean } = {}) =>
    request<{ detail: string }>(
      `users/${id}?delete_apps=${options.delete_apps ? "true" : "false"}`,
      { method: "DELETE" },
    ),

  /** Reverse-proxy configuration (administrators only). */
  getReverseProxySettings: () =>
    request<ReverseProxySettings>("settings/reverse-proxy"),

  updateReverseProxySettings: (input: UpdateReverseProxySettingsInput) =>
    request<ReverseProxySettings>("settings/reverse-proxy", {
      method: "PATCH",
      body: input,
    }),

  /** Server-provisioning provider + policy settings (administrators only). */
  getProvisioningSettings: () =>
    request<ProvisioningSettings>("settings/provisioning"),

  updateProvisioningSettings: (input: UpdateProvisioningSettingsInput) =>
    request<ProvisioningSettings>("settings/provisioning", {
      method: "PATCH",
      body: input,
    }),

  listProviderTemplates: () =>
    request<ProviderTemplates>("settings/provisioning/provider-templates"),

  syncJumpServerUsers: () =>
    request<{ results: JumpSyncEntry[] }>("settings/jump-server/sync", {
      method: "POST",
    }),

  changeJumpAccountMode: (input: JumpAccountModeInput) =>
    request<JumpAccountModeResult>("settings/jump-server/account-mode", {
      method: "POST",
      body: input,
    }),

  listServerTemplates: () =>
    request<ServerTemplate[]>("settings/server-templates"),

  createServerTemplate: (input: CreateServerTemplateInput) =>
    request<ServerTemplate>("settings/server-templates", {
      method: "POST",
      body: input,
    }),

  updateServerTemplate: (id: number, input: UpdateServerTemplateInput) =>
    request<ServerTemplate>(`settings/server-templates/${id}`, {
      method: "PATCH",
      body: input,
    }),

  deleteServerTemplate: (id: number) =>
    request<{ detail: string }>(`settings/server-templates/${id}`, {
      method: "DELETE",
    }),

  /** Per-user servers (admin, or the user themself). */
  listUserServers: (userId: number) =>
    request<UserServer[]>(`users/${userId}/servers`),

  /** Per-user provisioning usage vs. limits (create-form quota bars). */
  getUserServerUsage: (userId: number) =>
    request<ServerUsage>(`users/${userId}/servers/usage`),

  /** All servers the caller may see, grouped by owner (Servers view). */
  getServersOverview: () => request<ServersOverview>("servers/overview"),

  /** Historical usage stats for one server (Proxmox rrddata). */
  getServerStats: (userId: number, serverId: number, timeframe: string) =>
    request<ServerStats>(
      `users/${userId}/servers/${serverId}/stats?timeframe=${encodeURIComponent(timeframe)}`,
    ),

  createUserServer: (userId: number, input: CreateUserServerInput) =>
    request<UserServer>(`users/${userId}/servers`, {
      method: "POST",
      body: input,
    }),

  updateUserServer: (
    userId: number,
    serverId: number,
    input: UpdateUserServerInput,
  ) =>
    request<UserServer>(`users/${userId}/servers/${serverId}`, {
      method: "PATCH",
      body: input,
    }),

  /** Request deferred deletion (24h grace); returns the updated server. */
  deleteUserServer: (userId: number, serverId: number) =>
    request<UserServer>(`users/${userId}/servers/${serverId}`, {
      method: "DELETE",
    }),

  /** Cancel a pending deferred deletion; returns the updated server. */
  cancelServerDeletion: (userId: number, serverId: number) =>
    request<UserServer>(
      `users/${userId}/servers/${serverId}/cancel-deletion`,
      { method: "POST" },
    ),

  /** Admin-only: force-remove a server record (even if its destroy failed). */
  forceRemoveServer: (userId: number, serverId: number) =>
    request<{ detail: string }>(
      `users/${userId}/servers/${serverId}/force-remove`,
      { method: "POST" },
    ),

  listAccountServerTemplates: () =>
    request<ServerTemplateOption[]>("account/server-templates"),

  getAccountServerAccess: () =>
    request<ServerAccess>("account/server-access"),

  /** SSH key registry (Remote Access Config; administrators only). */
  listSshKeys: () => request<SshKey[]>("settings/ssh-keys"),

  createSshKey: (input: CreateSshKeyInput) =>
    request<SshKey>("settings/ssh-keys", { method: "POST", body: input }),

  updateSshKey: (id: number, input: UpdateSshKeyInput) =>
    request<SshKey>(`settings/ssh-keys/${id}`, {
      method: "PATCH",
      body: input,
    }),

  deleteSshKey: (id: number) =>
    request<{ detail: string }>(`settings/ssh-keys/${id}`, {
      method: "DELETE",
    }),

  /** Configurable branding (administrators only). */
  getBrandingSettings: () => request<BrandingSettings>("settings/branding"),

  updateBrandingSettings: (input: UpdateBrandingSettingsInput) =>
    request<BrandingSettings>("settings/branding", {
      method: "PATCH",
      body: input,
    }),

  /** Team management (administrators only; reads are open to any signed-in user). */
  listBundleTemplates: () =>
    request<BundleTemplate[]>("settings/bundle-templates"),

  createBundleTemplate: (input: CreateBundleTemplateInput) =>
    request<BundleTemplate>("settings/bundle-templates", {
      method: "POST",
      body: input,
    }),

  updateBundleTemplate: (id: number, input: UpdateBundleTemplateInput) =>
    request<BundleTemplate>(`settings/bundle-templates/${id}`, {
      method: "PATCH",
      body: input,
    }),

  deleteBundleTemplate: (id: number) =>
    request<{ detail: string }>(`settings/bundle-templates/${id}`, {
      method: "DELETE",
    }),

  cloneBundleTemplate: (id: number, name: string) =>
    request<BundleTemplate>(`settings/bundle-templates/${id}/clone`, {
      method: "POST",
      body: { name },
    }),

  setBundleTemplateEnabled: (id: number, enabled: boolean) =>
    request<BundleTemplate>(`settings/bundle-templates/${id}/enabled`, {
      method: "PATCH",
      body: { enabled },
    }),

  createTeam: (input: CreateTeamInput) =>
    request<Team>("settings/teams", { method: "POST", body: input }),

  updateTeam: (id: number, input: UpdateTeamInput) =>
    request<Team>(`settings/teams/${id}`, { method: "PATCH", body: input }),

  deleteTeam: (id: number) =>
    request<{ detail: string }>(`settings/teams/${id}`, { method: "DELETE" }),

  reorderTeams: (teamIds: number[]) =>
    request<Team[]>("settings/teams/reorder", {
      method: "POST",
      body: { team_ids: teamIds },
    }),
};
