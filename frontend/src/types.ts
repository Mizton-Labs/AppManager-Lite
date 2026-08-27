export type Role = "admin" | "user";

/** How an application's link is interpreted. */
export type UrlType = "url" | "alias" | "embedded";

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
  /** The user's own UI theme ("" = follow the deployment default). */
  theme?: string;
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
  /** Admin-selected default UI theme (fallback when the user has not chosen). */
  default_theme?: string;
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

export interface ProvisionResult {
  template_id: number;
  template_name: string;
  status: "created" | "failed" | "skipped";
  detail: string;
}

export interface GeneratedPassword {
  user: ApiUser;
  password: string;
  provisioning?: ProvisionResult[];
}

export type BundleMappingSource =
  | "username"
  | "user_id"
  | "user_apps_server"
  | "user_apps_server_host"
  | "user_apps_server_ip"
  | "user_role"
  // Dynamic per-template variables keyed by the server template slug:
  // server_<slug>_name / server_<slug>_ip / server_<slug>_user
  | `server_${string}_name`
  | `server_${string}_ip`
  | `server_${string}_user`;

export interface BundleTemplateMapping {
  field_name: string;
  source: BundleMappingSource;
}

export interface BundleTemplate {
  id: number;
  name: string;
  content: string;
  description: string;
  mappings: BundleTemplateMapping[];
  is_builtin: boolean;
  enabled: boolean;
  /** Read-only generic preview of the built-in's actual download; empty for
   * custom templates (whose `content` is already the real definition). */
  definition: string;
}

export interface BundleOption {
  id: number;
  name: string;
  description: string;
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

/** Per-server outcome of propagating a regenerated key. */
export interface ServerKeyRotation {
  server: string;
  ip_address: string;
  status: "updated" | "skipped" | "failed";
  detail: string;
}

export interface SshKeyRegenerateResult extends SshKeyInfo {
  rotation: ServerKeyRotation[];
}

/** Provider + policy settings; the API key itself is never returned.
 *
 * issue_021: the backend still accepts/returns a legacy
 * ``provisioning_allow_resource_edit`` field for backward compatibility, but
 * it is no longer read for authorization (any self-service user may edit
 * their own non-admin-managed server's resources) and is intentionally
 * omitted here so the UI no longer surfaces a now-inert toggle.
 */
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
  /** issue_025: auto-add created guests to their owner's Proxmox pool. */
  provisioning_add_to_pool: boolean;
  /** issue_025: admin-selected Proxmox realms and the pool-id prefix. */
  proxmox_realms: string[];
  proxmox_pool_prefix: string;
  provisioning_max_cpus: number;
  provisioning_max_memory_gb: number;
  provisioning_max_disk_gb: number;
  jump_enabled: boolean;
  jump_host: string;
  jump_user: string;
  jump_port: number;
  jump_ssh_key_id: number | null;
  jump_management_user: string;
  jump_account_mode: "per_user" | "shared";
  jump_jumper_user: string;
  jump_bundle_override: boolean;
  jump_bundle_host: string;
  jump_bundle_port: number;
}

export interface JumpSyncEntry {
  username: string;
  status: string;
  detail: string;
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
  provisioning_add_to_pool?: boolean;
  proxmox_realms?: string[];
  proxmox_pool_prefix?: string;
  provisioning_max_cpus?: number;
  provisioning_max_memory_gb?: number;
  provisioning_max_disk_gb?: number;
  jump_enabled?: boolean;
  jump_host?: string;
  jump_user?: string;
  jump_port?: number;
  jump_ssh_key_id?: number | null;
  jump_management_user?: string;
  jump_jumper_user?: string;
  jump_bundle_override?: boolean;
  jump_bundle_host?: string;
  jump_bundle_port?: number;
}

export interface JumpAccountModeInput {
  account_mode: "per_user" | "shared";
  jumper_user?: string;
  acknowledge_sync: boolean;
}

export interface JumpAccountModeResult {
  account_mode: "per_user" | "shared";
  reverted: boolean;
  detail: string;
  results: JumpSyncEntry[];
}

/** A Proxmox authentication realm offered for admin selection (issue_025). */
export interface ProxmoxRealm {
  realm: string;
  type: string;
  comment: string;
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

/** A registered SSH key (registry entry; never carries private material). */
export interface SshKey {
  id: number;
  name: string;
  kind: "path" | "stored";
  path: string;
  public_key: string;
  fingerprint: string;
  has_private_key: boolean;
}

export interface CreateSshKeyInput {
  name: string;
  kind: "path" | "stored";
  path?: string;
  private_key?: string;
}

export interface UpdateSshKeyInput {
  name?: string;
  kind?: "path" | "stored";
  path?: string;
  /** Write-only: send only to replace the stored private key. */
  private_key?: string;
}

/** An admin-registered Proxmox template used to create user servers. */
export interface ServerTemplate {
  id: number;
  vmid: number;
  name: string;
  kind: "lxc" | "vm";
  admin_ssh_key_path: string;
  admin_ssh_key_id: number | null;
  main_os_user: string;
  enable_sudo: boolean;
  enable_trusted_access: boolean;
  is_apps_server: boolean;
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
  // Deferred deletion (issue_015-r4 F1).
  deletion_requested_at: string;
  deletion_pending: boolean;
  deletion_failed: boolean;
  /** Only populated in administrator responses. */
  deletion_error: string;
  created_at: string;
  /** True right after a VM CPU/memory change; transient, never persisted. */
  reboot_required?: boolean;
  /** True when cloned from a template flagged as an apps server. */
  is_apps_server?: boolean;
  /** Live Proxmox pool membership, resolved on list; transient, never persisted. */
  poolid?: string;
  /** Live Proxmox presence classification, resolved on list; transient, never
   * persisted, and never used to auto-delete a record: "live" (confirmed
   * present), "missing" (an authoritative inventory read completed and the
   * guest was not found), or "unverified" (the provider is unconfigured,
   * unreachable, or the read failed -- absence is never inferred). Empty for
   * a server with no vmid yet, or a failed provisioning record. */
  presence?: "live" | "missing" | "unverified" | "";
  access_reset?: AccessResetOutcome[];
}

export interface AccessResetOutcome {
  target_type: "server" | "jump_server" | "trusted_mesh";
  target_name: string;
  account: string;
  status: string;
  detail: string;
}

export interface CreateUserServerInput {
  template_id: number;
  name: string;
  install_pubkey?: boolean;
  pubkey_users?: string;
}

/** A user's servers, for the Servers overview (issue_015-r5 F2). */
export interface OwnerServers {
  user_id: number;
  username: string;
  derived_user_id: string;
  servers: UserServer[];
}

export interface ServersOverview {
  is_admin: boolean;
  owners: OwnerServers[];
  /** Admin-only: every active account, including users with zero servers.
   * 0 for a non-admin caller. Optional for backward-compatible test fixtures;
   * always present in real API responses. */
  total_users?: number;
  /** Every visible server record (== sum of owners[].servers.length). */
  total_servers?: number;
}

/** One historical usage sample for a server's sparklines. */
export interface ServerStatsPoint {
  time: number;
  cpu_pct: number;
  mem: number;
  maxmem: number;
  disk: number;
  maxdisk: number;
  netin: number;
  netout: number;
}

export interface ServerStats {
  available: boolean;
  detail: string;
  timeframe: string;
  points: ServerStatsPoint[];
}

export type StatsTimeframe = "hour" | "day" | "week";

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
  is_apps_server: boolean;
}

export interface ServerAccess {
  can_create: boolean;
  reason: string;
  allow_resource_edit: boolean;
}

/** One resource dimension for the create-form quota bars (issue_015-r4 F3). */
export interface ResourceUsage {
  used: number;
  limit: number;
}

/** Per-user provisioning usage vs. limits. `unlimited` is true for admins. */
export interface ServerUsage {
  unlimited: boolean;
  servers: ResourceUsage;
  cpus: ResourceUsage;
  memory_gb: ResourceUsage;
  disk_gb: ResourceUsage;
}

export interface CreateServerTemplateInput {
  vmid: number;
  name: string;
  kind: "lxc" | "vm";
  admin_ssh_key_path?: string;
  admin_ssh_key_id?: number | null;
  main_os_user?: string;
  enable_sudo?: boolean;
  enable_trusted_access?: boolean;
  is_apps_server?: boolean;
}

export interface UpdateServerTemplateInput {
  vmid?: number;
  name?: string;
  kind?: "lxc" | "vm";
  admin_ssh_key_path?: string;
  admin_ssh_key_id?: number | null;
  main_os_user?: string;
  enable_sudo?: boolean;
  enable_trusted_access?: boolean;
  is_apps_server?: boolean;
}

export interface CreateBundleTemplateInput {
  name: string;
  content: string;
  description?: string;
  mappings: BundleTemplateMapping[];
}

export interface UpdateBundleTemplateInput {
  name?: string;
  content?: string;
  description?: string;
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
  provision_templates?: number[];
}

export interface UpdateUserInput {
  /** Sign-in email. Mutable; the immutable `user_id` (server names, SSH
   * users, pools, jump accounts) never changes when this changes. */
  username?: string;
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
  reverse_proxy_ssh_key_id: number | null;
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
  reverse_proxy_ssh_key_id?: number | null;
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
  default_theme: string;
  configured: boolean;
}

export interface UpdateBrandingSettingsInput {
  app_name?: string;
  app_logo?: string;
  collaborators?: string[];
  default_theme?: string;
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
  /** Rewrite root-absolute paths for apps that assume they run at '/'. */
  apps_rewrite_root?: boolean;
  /** Forward the authenticated user's stored username/email upstream as
   * `X-AppManager-User` (alias apps only; requires alias authentication). */
  pass_authenticated_user?: boolean;
  /** A staged alias change awaiting approval; management/own-app responses only. */
  pending_alias?: string;
  /** A staged enable/disable change awaiting approval; management/own-app only. */
  pending_is_active?: boolean | null;
  /** A staged alias auth change awaiting approval; management/own-app only. */
  pending_alias_auth_required?: boolean | null;
  /** A staged rewrite-root change awaiting approval; management/own-app only. */
  pending_apps_rewrite_root?: boolean | null;
  /** A staged authenticated-user header change awaiting approval; management/own-app only. */
  pending_pass_authenticated_user?: boolean | null;
  /** True when current approved proxy config needs an admin push. */
  needs_push?: boolean;
  is_favorite?: boolean;
  visits_7d?: number | null;
  show_statistics?: boolean;
  is_private?: boolean;
  shared_users?: ApplicationShareUser[];
}

export interface ApplicationShareUser { id: number; username: string; user_id: string; }

export interface ApplicationTrendPoint { date: string; launches: number; unique_users: number; }
export interface ApplicationStatisticsRow {
  application_id: number; name: string; launches: number; unique_users: number;
  favorites: number; visits_7d: number;
  /** Authorized alias visits (direct/deep-link/iframe navigation via nginx,
   * separate from the portal card-click "launches" above). */
  alias_visits: number;
  unique_alias_users: number;
  anonymous_alias_visits: number;
}
export interface ApplicationStatistics {
  days: number; launches: number; unique_users: number; favorites: number;
  trend: ApplicationTrendPoint[]; applications: ApplicationStatisticsRow[];
  app_trends: ApplicationTrendSeries[]; user_activity: UserActivityRow[];
  alias_visits: number;
  unique_alias_users: number;
  anonymous_alias_visits: number;
  /** issue_local_032: complete (uncapped) drill-down lists for the
   * corresponding clickable KPI card. */
  launch_users: LaunchUserRow[];
  favorite_entries: FavoriteEntryRow[];
  alias_users: AliasUserRow[];
}
export interface LaunchUserRow {
  user_id: string; launches: number; applications_used: number;
  active_days: number; last_activity: string;
}
export interface FavoriteEntryRow {
  application_id: number; application_name: string; user_id: string; starred_at: string;
}
export interface AliasUserRow {
  user_id: string; alias_visits: number; applications_visited: number;
  active_days: number; last_visit: string;
}
export interface ApplicationTrendSeries { application_id: number; name: string; launches: number; points: ApplicationTrendPoint[]; }
export interface UserActivityRow { user_id: string; launches: number; applications_used: number; }
export interface ApplicationUserActivity { user_id: string; launches: number; active_days: number; last_activity: string; }
export interface ApplicationFavoriteUser { user_id: string; starred_at: string; }
export interface ApplicationStatisticsDetail { application_id: number; activity_users: ApplicationUserActivity[]; favorite_users: ApplicationFavoriteUser[]; }
export interface ApplicationStatisticsSettings { show_app_statistics: boolean; }

export interface AliasConfig {
  status: string;
  log: string;
  alias: string;
  apps_protocol: "http" | "https";
  apps_server: string;
  apps_port: string;
  apps_path: string;
  alias_auth_required: boolean;
  apps_rewrite_root: boolean;
  pass_authenticated_user: boolean;
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
  apps_rewrite_root?: boolean;
  pass_authenticated_user?: boolean;
  is_private?: boolean;
  shared_user_ids?: number[];
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
  apps_rewrite_root?: boolean;
  pass_authenticated_user?: boolean;
  is_private?: boolean;
  shared_user_ids?: number[];
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
