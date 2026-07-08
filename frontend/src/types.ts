export type Role = "admin" | "user";

/** How an application's link is interpreted. */
export type UrlType = "url" | "alias";

/** Lifecycle state of a submitted application. */
export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface ApiUser {
  id: number;
  username: string;
  /** Derived identifier: email local part with dots/underscores as dashes. */
  user_id: string;
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
  auth_method: "local" | "oidc" | "saml";
  /** Configurable branding, present even before authentication. */
  app_name: string;
  app_logo: string;
  /** Admin-managed About-page collaborators (separate from the dev team). */
  collaborators: string[];
  /** One-time setup flag that drives the first-login wizard (admins only). */
  configured: boolean;
}


export interface SsoProvider {
  protocol: "oidc" | "saml";
  label: string;
  login_url: string;
}


export interface SsoConfig {
  enabled: boolean;
  local_login_enabled: boolean;
  providers: SsoProvider[];
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

/** Public half of the account's SSH keypair (never the private key). */
export interface SshKeyInfo {
  user_id: string;
  public_key: string;
  generated_at: string | null;
}

/** Provider + policy settings; the API key itself is never returned. */
export interface ProvisioningSettings {
  provider_type: string;
  proxmox_url: string;
  proxmox_token_name: string;
  proxmox_api_key_set: boolean;
  proxmox_template_filter: string;
  proxmox_templates_only: boolean;
  proxmox_verify_tls: boolean;
  proxmox_conn_status: string;
  proxmox_conn_log: string;
  provisioning_self_service: boolean;
  provisioning_max_servers: number;
  provisioning_allow_resource_edit: boolean;
  provisioning_max_cpus: number;
  provisioning_max_memory_gb: number;
  provisioning_max_disk_gb: number;
}

export interface UpdateProvisioningSettingsInput {
  provider_type?: string;
  proxmox_url?: string;
  proxmox_token_name?: string;
  /** Write-only: sent only when the administrator enters a new key. */
  proxmox_api_key?: string;
  proxmox_template_filter?: string;
  proxmox_templates_only?: boolean;
  proxmox_verify_tls?: boolean;
  provisioning_self_service?: boolean;
  provisioning_max_servers?: number;
  provisioning_allow_resource_edit?: boolean;
  provisioning_max_cpus?: number;
  provisioning_max_memory_gb?: number;
  provisioning_max_disk_gb?: number;
}

/** A VM/LXC entry read live from the provider. */
export interface ProviderTemplate {
  vmid: number;
  name: string;
  kind: "lxc" | "vm";
  node: string;
  is_template: boolean;
}

export interface ProviderTemplates {
  status: string;
  log: string;
  templates: ProviderTemplate[];
}

/** An admin-registered Proxmox template used to create user servers. */
export interface ServerTemplate {
  id: number;
  vmid: number;
  name: string;
  kind: "lxc" | "vm";
  admin_ssh_key_path: string;
}

/** A user's provisioned (or referenced) LXC/VM server. */
export interface UserServer {
  id: number;
  user_id: number;
  name: string;
  hostname: string;
  template_id: number | null;
  template_name: string;
  vmid: number | null;
  node: string;
  kind: "lxc" | "vm";
  ip_address: string;
  cpus: number;
  memory_gb: number;
  disk_gb: number;
  admin_modified: boolean;
  status: "created" | "reference" | "failed";
  last_log: string;
  created_at: string;
}

export interface CreateUserServerInput {
  template_id: number;
  name: string;
  install_pubkey?: boolean;
  pubkey_users?: string;
}

export interface UpdateUserServerInput {
  ip_address?: string;
  cpus?: number;
  memory_gb?: number;
  disk_gb?: number;
}

/** User-facing template option (no vmid or key paths). */
export interface ServerTemplateOption {
  id: number;
  name: string;
  kind: "lxc" | "vm";
}

export interface ServerAccess {
  can_create: boolean;
  reason: string;
}

export interface CreateServerTemplateInput {
  vmid: number;
  name: string;
  kind: "lxc" | "vm";
  admin_ssh_key_path?: string;
}

export interface UpdateServerTemplateInput {
  vmid?: number;
  name?: string;
  kind?: "lxc" | "vm";
  admin_ssh_key_path?: string;
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
  appmanager_proxy_host: string;
  appmanager_proxy_port: string;
  alias_template: string;
  protected_alias_auth_status: string;
  protected_alias_auth_log: string;
}

export interface UpdateReverseProxySettingsInput {
  nginx_host?: string;
  nginx_user?: string;
  nginx_conf_path?: string;
  ssh_key_path?: string;
  appmanager_proxy_host?: string;
  appmanager_proxy_port?: string;
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
  /** Creator's username, shown as publisher metadata in listings. */
  created_by: string | null;
  created_by_id?: number | null;
  /** First team assigned to the publisher; independent from shared visibility teams. */
  publisher_team?: string;
  /** Last reverse-proxy push status; only in management/own-app responses. */
  last_push_status?: string | null;
  last_push_log?: string;
  last_push_at?: string | null;
  /** Per-app apps server/port (alias apps); management/own-app responses only. */
  apps_server?: string;
  apps_protocol?: "http" | "https";
  apps_port?: string;
  apps_path?: string;
  /** Whether AppManager auth protects the alias before proxying. */
  alias_auth_required: boolean;
  /** A staged alias change awaiting approval; management/own-app responses only. */
  pending_alias?: string;
  /** A staged enable/disable change awaiting approval; management/own-app only. */
  pending_is_active?: boolean | null;
  /** A staged alias auth change awaiting approval; management/own-app only. */
  pending_alias_auth_required?: boolean | null;
  /** True when current approved proxy config needs an admin push. */
  needs_push?: boolean;
}

export interface AliasConfig {
  status: string;
  log: string;
  alias: string;
  apps_protocol: "http" | "https";
  apps_server: string;
  apps_port: string;
  apps_path: string;
  alias_auth_required: boolean;
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
  apps_protocol?: "http" | "https";
  apps_port?: string;
  apps_path?: string;
  alias_auth_required?: boolean;
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
  apps_protocol?: "http" | "https";
  apps_port?: string;
  apps_path?: string;
  alias_auth_required?: boolean;
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
