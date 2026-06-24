import type {
  ApiUser,
  Application,
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
  SessionState,
  SsoConfig,
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
