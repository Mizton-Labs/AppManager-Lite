/**
 * Canonical team list, mirroring the backend `app/teams.py` `DEFAULT_TEAMS`.
 *
 * Used to build the sidebar and team routes. Server-side enforcement of
 * team-based access arrives with real team content in a later phase; for now
 * the sidebar only controls which team sections each account can see.
 */
export const ALL_TEAMS = [
  "Detect and Response",
  "Threat Hunting",
  "Threat Intel",
  "Forensics & BID",
  "Advanced Analytics",
  "Red Team",
  "Threat Detection Engineering",
] as const;

/** Build a URL-safe slug for a team name, e.g. "Red Team" -> "red-team". */
export function teamSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Resolve a slug back to a known team name, or `null` when unknown. */
export function teamFromSlug(
  slug: string,
  teams: readonly string[] = ALL_TEAMS,
): string | null {
  return teams.find((team) => teamSlug(team) === slug) ?? null;
}
