/**
 * Application branding.
 *
 * Branding (the application name and logo) is configured by an administrator in
 * General Settings and delivered with every session response, so it is known
 * even before authentication. A module-level store holds the current values;
 * {@link setBranding} is called whenever the session is (re)loaded.
 *
 * When no logo has been configured, a bundled default asset is served from the
 * frontend `public/` directory. When no name has been configured, a neutral
 * default label is used.
 */

/** Neutral fallback name used until an administrator configures branding. */
export const DEFAULT_APP_NAME = "AppManager Lite";

/** Browseable URL of the source repository (shown on the About page). */
export const GITHUB_URL =
  "https://github.com/Mizton-Labs/AppManager-Lite";

/** Bundled fallback logo asset filename, relative to the deployment base. */
const DEFAULT_LOGO_ASSET = "app-logo.svg";

interface Branding {
  appName: string;
  appLogo: string;
  collaborators: string[];
}

const current: Branding = { appName: "", appLogo: "", collaborators: [] };

/** Update the in-memory branding from a session response. */
export function setBranding(branding: {
  app_name?: string | null;
  app_logo?: string | null;
  collaborators?: string[] | null;
}): void {
  current.appName = (branding.app_name ?? "").trim();
  current.appLogo = (branding.app_logo ?? "").trim();
  current.collaborators = (branding.collaborators ?? []).filter(Boolean);
  document.title = getAppName();
  applyFavicon();
}

/** The configured application name, or the neutral default when unset. */
export function getAppName(): string {
  return current.appName || DEFAULT_APP_NAME;
}

/** Admin-configured About-page collaborators (may be empty). */
export function getCollaborators(): string[] {
  return current.collaborators;
}

/**
 * Resolve the logo source. A configured data-URI or absolute URL is used as-is;
 * a configured relative path or the bundled default is resolved against the
 * document base URI so it loads under any deployment path prefix.
 */
export function getLogoSrc(): string {
  const value = current.appLogo;
  if (value.startsWith("data:") || /^https?:\/\//i.test(value)) {
    return value;
  }
  const asset = value || DEFAULT_LOGO_ASSET;
  try {
    return new URL(asset, document.baseURI).toString();
  } catch {
    return asset;
  }
}

/**
 * Point the document favicon at the current logo. Called at startup and again
 * whenever branding changes.
 */
export function applyFavicon(): void {
  const existing = document.querySelector<HTMLLinkElement>("link[rel='icon']");
  const link = existing ?? document.createElement("link");
  link.rel = "icon";
  const href = getLogoSrc();
  // Only SVG assets carry an explicit type; data URIs and raster URLs do not.
  if (href.endsWith(".svg")) {
    link.type = "image/svg+xml";
  } else {
    link.removeAttribute("type");
  }
  link.href = href;
  if (!link.isConnected) {
    document.head.appendChild(link);
  }
}
