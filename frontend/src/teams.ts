/**
 * Team URL-slug helpers.
 *
 * Teams are administrator-managed and fetched from the backend, so there is no
 * hardcoded team list here. These helpers translate between a team name and the
 * URL-safe slug used in `/teams/:slug` routes; callers pass the current team
 * list (e.g. from the session-scoped sidebar) for the reverse lookup.
 */

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
  teams: readonly string[],
): string | null {
  return teams.find((team) => teamSlug(team) === slug) ?? null;
}
