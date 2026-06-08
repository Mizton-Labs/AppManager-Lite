import type { Application } from "../types";

/**
 * Resolve an application's link to an absolute href.
 *
 * Full-URL applications are linked verbatim. "Local alias" applications store a
 * relative reference that is resolved against the document base URI (the
 * backend-injected `<base href>` matching the deployment prefix), so an external
 * reverse proxy can map the path to the real service. Resolution failures fall
 * back to the stored value rather than throwing.
 */
export function resolveAppHref(app: Pick<Application, "url" | "url_type">): string {
  if (app.url_type === "alias") {
    try {
      return new URL(app.url, document.baseURI).toString();
    } catch {
      return app.url;
    }
  }
  return app.url;
}

/**
 * Resolve an application icon/logo value to a loadable `src`.
 *
 * Inline data URIs and absolute URLs (http(s) or any scheme) are returned
 * unchanged. A bundled default-logo path is stored relative (e.g.
 * `logos/red-team-2.svg`) and is resolved against the document base URI at
 * render time, so it loads correctly under any origin or deployment path
 * prefix. An empty value yields an empty string (callers show a fallback).
 */
export function resolveIconSrc(iconUrl: string): string {
  if (!iconUrl) {
    return "";
  }
  // data: URIs and anything with an explicit scheme (e.g. https:) pass through.
  if (iconUrl.startsWith("data:") || /^[a-z][a-z0-9+.-]*:/i.test(iconUrl)) {
    return iconUrl;
  }
  try {
    return new URL(iconUrl, document.baseURI).toString();
  } catch {
    return iconUrl;
  }
}
