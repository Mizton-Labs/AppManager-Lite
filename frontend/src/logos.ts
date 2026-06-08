/**
 * Default application-logo catalogue.
 *
 * A set of bundled default logos used when an application is created without an
 * uploaded logo. The SVG assets live in `public/logos/` and are named
 * `<set-slug>-<1..3>.svg` (plus a neutral `generic-<1..3>.svg` set). Selection
 * is deterministic so a given application always shows the same default and the
 * three variants are spread evenly.
 *
 * Teams are administrator-managed and arbitrary, so an application's first team
 * is mapped to a bundled set only when its slug matches one of the catalogue
 * sets below; otherwise the neutral `generic` set is used.
 */
import { teamSlug } from "./teams";

/** How many default variants exist per set; mirrors the generated assets. */
const VARIANTS_PER_SET = 3;

/** Slug used for the neutral, team-less default set. */
const GENERIC_SLUG = "generic";

/**
 * Slugs that have a bundled per-set logo catalogue under `public/logos/`. A
 * team whose slug is not listed here falls back to the neutral generic set.
 */
const KNOWN_LOGO_SETS: ReadonlySet<string> = new Set([
  "detect-response",
  "threat-hunting",
  "threat-intel",
  "forensics-bid",
  "advanced-analytics",
  "red-team",
  "threat-detection-engineering",
]);

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
 * teams whose slug matches a bundled catalogue set; falls back to the neutral
 * generic set otherwise (including when the app has no team).
 */
function setSlugFor(teams: readonly string[]): string {
  for (const team of teams) {
    const slug = teamSlug(team);
    if (KNOWN_LOGO_SETS.has(slug)) {
      return slug;
    }
  }
  return GENERIC_SLUG;
}

/**
 * Resolve a default logo for an application, picked deterministically from the
 * relevant set by hashing the application name. A **relative** path is returned
 * (e.g. `logos/red-team-2.svg`) and stored in the application's `icon_url` at
 * create time; it is resolved against `document.baseURI` only at render time
 * (see {@link resolveIconSrc}), so the logo loads correctly whatever origin or
 * deployment path prefix the portal is served from.
 */
export function defaultLogoFor(
  appName: string,
  teams: readonly string[],
): string {
  const slug = setSlugFor(teams);
  const index = (stableHash(appName) % VARIANTS_PER_SET) + 1;
  return `logos/${slug}-${index}.svg`;
}
