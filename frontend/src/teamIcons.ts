/**
 * Bundled team-icon catalogue.
 *
 * Teams may show a small icon on their sidebar button. An administrator picks
 * one from this generic-IT catalogue (or uploads a small raster image, or
 * leaves it unset). The SVG assets live in `public/team-icons/` and are
 * referenced by a **relative** path (e.g. `team-icons/server.svg`) that the
 * backend validates and that is resolved against `document.baseURI` at render
 * time, so it loads under any deployment path prefix.
 *
 * The icons are intentionally generic IT (not security-specific): server,
 * database, network, cloud, and so on. When a team has no icon configured, the
 * neutral {@link DEFAULT_TEAM_ICON} is shown.
 */

/** Catalogue entry: a stable id, the relative asset path, and a label. */
export interface TeamIconOption {
  id: string;
  path: string;
  label: string;
}

/** Relative path of the neutral fallback used when a team has no icon. */
export const DEFAULT_TEAM_ICON = "team-icons/default.svg";

/** The selectable catalogue, in display order. */
export const TEAM_ICON_CATALOGUE: readonly TeamIconOption[] = [
  { id: "server", path: "team-icons/server.svg", label: "Server" },
  { id: "database", path: "team-icons/database.svg", label: "Database" },
  { id: "network", path: "team-icons/network.svg", label: "Network" },
  { id: "cloud", path: "team-icons/cloud.svg", label: "Cloud" },
  { id: "shield", path: "team-icons/shield.svg", label: "Security" },
  { id: "dashboard", path: "team-icons/dashboard.svg", label: "Dashboard" },
  { id: "code", path: "team-icons/code.svg", label: "Development" },
  { id: "support", path: "team-icons/support.svg", label: "Support" },
  { id: "storage", path: "team-icons/storage.svg", label: "Storage" },
  { id: "container", path: "team-icons/container.svg", label: "Containers" },
  { id: "automation", path: "team-icons/automation.svg", label: "Automation" },
  { id: "users", path: "team-icons/users.svg", label: "Team" },
] as const;

/** Resolve a team's icon path, falling back to the neutral default when unset. */
export function teamIconOrDefault(icon: string | undefined | null): string {
  const value = (icon ?? "").trim();
  return value || DEFAULT_TEAM_ICON;
}
