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
 * The catalogue includes a generic-IT set (server, database, network, cloud,
 * and so on) and a cybersecurity set (defensive/offensive security,
 * vulnerability, threat intel, DevOps, security engineering — three variants
 * each). When a team has no icon configured, the neutral
 * {@link DEFAULT_TEAM_ICON} is shown.
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

  // Cybersecurity set (three variants per category).
  {
    id: "defensive-security-1",
    path: "team-icons/defensive-security-1.svg",
    label: "Defensive Security 1",
  },
  {
    id: "defensive-security-2",
    path: "team-icons/defensive-security-2.svg",
    label: "Defensive Security 2",
  },
  {
    id: "defensive-security-3",
    path: "team-icons/defensive-security-3.svg",
    label: "Defensive Security 3",
  },
  {
    id: "offensive-security-1",
    path: "team-icons/offensive-security-1.svg",
    label: "Offensive Security 1",
  },
  {
    id: "offensive-security-2",
    path: "team-icons/offensive-security-2.svg",
    label: "Offensive Security 2",
  },
  {
    id: "offensive-security-3",
    path: "team-icons/offensive-security-3.svg",
    label: "Offensive Security 3",
  },
  {
    id: "vulnerability-1",
    path: "team-icons/vulnerability-1.svg",
    label: "Vulnerability 1",
  },
  {
    id: "vulnerability-2",
    path: "team-icons/vulnerability-2.svg",
    label: "Vulnerability 2",
  },
  {
    id: "vulnerability-3",
    path: "team-icons/vulnerability-3.svg",
    label: "Vulnerability 3",
  },
  {
    id: "threat-intel-1",
    path: "team-icons/threat-intel-1.svg",
    label: "Threat Intel 1",
  },
  {
    id: "threat-intel-2",
    path: "team-icons/threat-intel-2.svg",
    label: "Threat Intel 2",
  },
  {
    id: "threat-intel-3",
    path: "team-icons/threat-intel-3.svg",
    label: "Threat Intel 3",
  },
  { id: "devops-1", path: "team-icons/devops-1.svg", label: "DevOps 1" },
  { id: "devops-2", path: "team-icons/devops-2.svg", label: "DevOps 2" },
  { id: "devops-3", path: "team-icons/devops-3.svg", label: "DevOps 3" },
  {
    id: "security-engineering-1",
    path: "team-icons/security-engineering-1.svg",
    label: "Security Engineering 1",
  },
  {
    id: "security-engineering-2",
    path: "team-icons/security-engineering-2.svg",
    label: "Security Engineering 2",
  },
  {
    id: "security-engineering-3",
    path: "team-icons/security-engineering-3.svg",
    label: "Security Engineering 3",
  },
] as const;

/** Resolve a team's icon path, falling back to the neutral default when unset. */
export function teamIconOrDefault(icon: string | undefined | null): string {
  const value = (icon ?? "").trim();
  return value || DEFAULT_TEAM_ICON;
}
