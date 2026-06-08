/**
 * Default application-logo catalogue.
 *
 * issue_005 asks for a set of bundled default logos -- three per team, themed to
 * each team's nature -- used when an application is created without an uploaded
 * logo. The SVG assets live in `public/logos/` and are named
 * `<team-slug>-<1..3>.svg` (plus a neutral `generic-<1..3>.svg` set for apps
 * with no team). Selection is deterministic so a given application always shows
 * the same default and the three variants are spread evenly.
 */
import { ALL_TEAMS, teamSlug } from "./teams";

/** How many default variants exist per set; mirrors the generated assets. */
const VARIANTS_PER_SET = 3;

/** Slug used for the neutral, team-less default set. */
const GENERIC_SLUG = "generic";

/**
 * Deterministic, order-independent string hash (FNV-1a, 32-bit). Used to pick a
 * stable default variant for an application so re-renders and reloads never
 * change the logo.
 */
export function stableHash(value: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    // 32-bit FNV prime multiply via shifts, kept in unsigned range.
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

/**
 * Choose the default-logo set for an application. Uses the first of the app's
 * teams in canonical order; falls back to the neutral generic set when the app
 * has no recognised team.
 */
function setSlugFor(teams: readonly string[]): string {
  const chosen = ALL_TEAMS.find((team) => teams.includes(team));
  return chosen ? teamSlug(chosen) : GENERIC_SLUG;
}

/**
 * Resolve a default logo for an application, picked deterministically from the
 * relevant team's set by hashing the application name. A **relative** path is
 * returned (e.g. `logos/red-team-2.svg`) and stored in the application's
 * `icon_url` at create time; it is resolved against `document.baseURI` only at
 * render time (see {@link resolveIconSrc}), so the logo loads correctly whatever
 * origin or deployment path prefix the portal is served from.
 */
export function defaultLogoFor(
  appName: string,
  teams: readonly string[],
): string {
  const slug = setSlugFor(teams);
  const index = (stableHash(appName) % VARIANTS_PER_SET) + 1;
  return `logos/${slug}-${index}.svg`;
}
