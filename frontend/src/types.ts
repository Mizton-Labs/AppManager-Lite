export type Role = "admin" | "user";

/** How an application's link is interpreted. */
export type UrlType = "url" | "alias";

/** Lifecycle state of a submitted application. */
export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface ApiUser {
  id: number;
  username: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  self_service: boolean;
  apps_server: string;
  apps_server_ip: string;
  teams: string[];
}

export interface SessionState {
  authenticated: boolean;
  enable_auth: boolean;
  user: ApiUser | null;
  csrf_token: string | null;
  /** Configurable branding, present even before authentication. */
  app_name: string;
  app_logo: string;
  /** Admin-managed About-page collaborators (separate from the dev team). */
  collaborators: string[];
  /** One-time setup flag that drives the first-login wizard (admins only). */
  configured: boolean;
}

export interface GeneratedPassword {
  user: ApiUser;
  password: string;
}

export type BundleMappingSource =
  | "username"
  | "user_apps_server"
  | "user_apps_server_host"
  | "user_apps_server_ip"
  | "user_role";

export interface BundleTemplateMapping {
  field_name: string;
  source: BundleMappingSource;
}

export interface BundleTemplate {
  id: number;
  name: string;
  content: string;
  mappings: BundleTemplateMapping[];
}

export interface BundleOption {
  id: number;
  name: string;
}

export interface BundleDownload {
  content: string;
  filename: string;
}

export interface CreateBundleTemplateInput {
  name: string;
  content: string;
  mappings: BundleTemplateMapping[];
}

export interface UpdateBundleTemplateInput {
  name?: string;
  content?: string;
  mappings?: BundleTemplateMapping[];
}

/** An administrator-managed team (sidebar section + membership scope). */
export interface Team {
  id: number;
  name: string;
  sort_order: number;
  /** Optional small icon: a bundled catalogue path or a raster data URI. */
  icon: string;
}

export interface CreateTeamInput {
  name: string;
  icon?: string;
}

export interface UpdateTeamInput {
  name?: string;
  icon?: string;
}

export interface CreateUserInput {
  username: string;
  role: Role;
  teams: string[];
  self_service?: boolean;
  apps_server?: string;
  apps_server_ip?: string;
}

export interface UpdateUserInput {
  role?: Role;
  teams?: string[];
  is_active?: boolean;
  self_service?: boolean;
  apps_server?: string;
  apps_server_ip?: string;
}

/** Reverse-proxy (nginx) configuration in General Settings (admin only). */
export interface ReverseProxySettings {
  nginx_host: string;
  nginx_user: string;
  nginx_conf_path: string;
  ssh_key_path: string;
  alias_template: string;
}

export interface UpdateReverseProxySettingsInput {
  nginx_host?: string;
  nginx_user?: string;
  nginx_conf_path?: string;
  ssh_key_path?: string;
  alias_template?: string;
}

/** Configurable branding in General Settings (admin only). */
export interface BrandingSettings {
  app_name: string;
  app_logo: string;
  /** Admin-managed About-page collaborators (separate from the dev team). */
  collaborators: string[];
  configured: boolean;
}

export interface UpdateBrandingSettingsInput {
  app_name?: string;
  app_logo?: string;
  collaborators?: string[];
  configured?: boolean;
}

export interface Application {
  id: number;
  name: string;
  description: string;
  url: string;
  url_type: UrlType;
  icon_url: string;
  teams: string[];
  is_active: boolean;
  approval_status: ApprovalStatus;
  sort_order: number;
  /** Creator's username; only populated in own-app and management responses. */
  created_by: string | null;
  created_by_id?: number | null;
  /** Last reverse-proxy push status; only in management/own-app responses. */
  last_push_status?: string | null;
  last_push_log?: string;
  last_push_at?: string | null;
  /** Per-app apps server/port (alias apps); management/own-app responses only. */
  apps_server?: string;
  apps_port?: string;
  /** A staged alias change awaiting approval; management/own-app responses only. */
  pending_alias?: string;
  /** A staged enable/disable change awaiting approval; management/own-app only. */
  pending_is_active?: boolean | null;
  /** True when current approved proxy config needs an admin push. */
  needs_push?: boolean;
}

export interface CreateApplicationInput {
  name: string;
  url: string;
  url_type?: UrlType;
  description?: string;
  icon_url?: string;
  teams: string[];
  is_active?: boolean;
  sort_order?: number;
  apps_server?: string;
  apps_port?: string;
  created_by?: number;
}

export interface UpdateApplicationInput {
  name?: string;
  url?: string;
  url_type?: UrlType;
  description?: string;
  icon_url?: string;
  teams?: string[];
  is_active?: boolean;
  approval_status?: ApprovalStatus;
  sort_order?: number;
  apps_server?: string;
  apps_port?: string;
}

/** Which subsystem an audit entry belongs to (one per audit-view tab). */
export type AuditCategory = "application" | "user" | "system";

export interface AuditEntry {
  id: number;
  created_at: string;
  category: AuditCategory;
  action: string;
  actor_username: string | null;
  target_type: string | null;
  target_id: number | null;
  target_name: string | null;
  detail: string;
}
