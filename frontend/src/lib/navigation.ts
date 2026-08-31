/**
 * Shared validation for a post-login/authenticated-bootstrap redirect
 * target ("next"), mirroring the backend's `sso.safe_return_to`.
 *
 * Only a same-origin, single-segment-rooted relative path (optionally with a
 * query string and/or fragment) is accepted. Rejects anything a browser could
 * interpret as a different origin: absolute URLs, protocol-relative
 * ("//host") URLs, backslashes (some browsers normalize a leading "/\\" the
 * same as "//"), and control characters.
 */
export function safeNextPath(value: string | null | undefined): string {
  const next = (value ?? "").trim();
  if (!next.startsWith("/")) return "";
  if (next.startsWith("//")) return "";
  if (next.includes("\\")) return "";
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f]/.test(next)) return "";
  return next;
}

/** Read and validate the `next` query parameter from the current location. */
export function safeNextFromLocation(): string {
  return safeNextPath(new URLSearchParams(window.location.search).get("next"));
}

/**
 * True when the current document is the portal's landing page (its base
 * path, e.g. "/" or "/home/" for a prefixed deployment), regardless of query
 * string. Used to gate the authenticated-bootstrap redirect so a `next`
 * parameter lingering on some other SPA route is never acted on.
 *
 * The backend always injects a real `<base href>` (matching APP_BASE_PREFIX)
 * into the served HTML; fail closed (return false) if that tag is ever
 * missing rather than falling back to `document.baseURI`'s default of "the
 * current document's own URL" -- that fallback would make every route look
 * like the landing page and defeat this gate entirely.
 */
export function isPortalLandingPage(): boolean {
  const baseEl = document.querySelector("base[href]");
  if (!baseEl) return false;
  const base = new URL(document.baseURI).pathname;
  return window.location.pathname === base;
}
