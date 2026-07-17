import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { Application } from "../types";
import { resolveAppHref, resolveIconSrc } from "../lib/links";

/**
 * Renders an embedded application (url_type === "embedded") inside the portal.
 *
 * An embedded app frames one of the owner's existing aliases: its stored `url`
 * is the alias slug, and the iframe loads the SAME-ORIGIN alias path served by
 * the reverse proxy under the portal's own domain (resolved against the
 * deployment base). This is the only source reachable by external users and
 * free of mixed content -- the browser only ever talks to the portal origin,
 * and the alias's nginx proxy relays the internal service server-side.
 *
 * Access is enforced by the backend: the app only appears in the caller's
 * visible list if their team/user/private grants allow it.
 */
export function EmbeddedAppView({ collapsed }: { collapsed: boolean }) {
  const { id } = useParams();
  const appId = Number(id);
  const [app, setApp] = useState<Application | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .listApplications()
      .then((apps) => {
        if (!active) return;
        const match = apps.find(
          (a) => a.id === appId && a.url_type === "embedded",
        );
        if (!match) {
          setError("This embedded application is not available.");
          setApp(null);
        } else {
          setApp(match);
        }
      })
      .catch((err) => {
        if (!active) return;
        setError(
          err instanceof ApiError
            ? err.message
            : "Unable to load the embedded application.",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [appId]);

  if (loading) {
    return (
      <div className="embedded-view">
        <p role="status">Loading application…</p>
      </div>
    );
  }
  if (error || !app) {
    return (
      <div className="embedded-view">
        <p className="alert error" role="alert">
          {error ?? "This embedded application is not available."}
        </p>
      </div>
    );
  }

  const icon = resolveIconSrc(app.icon_url);
  return (
    <div className={collapsed ? "embedded-view wide" : "embedded-view"}>
      <div className="embedded-topbar">
        {icon ? (
          <img src={icon} alt="" width={20} height={20} />
        ) : null}
        <span className="embedded-title">{app.name}</span>
      </div>
      <iframe
        className="embedded-frame"
        // The stored url is an alias slug; render the same-origin alias path
        // (resolved against the deployment base) served by the reverse proxy.
        src={resolveAppHref({ url: app.url, url_type: "alias" })}
        title={app.name}
        // allow-same-origin is retained deliberately: the framed path /<slug>/
        // is nginx-proxied to the owner's own internal service (an approved
        // alias), NOT AppManager's SPA -- alias slugs are a strict [A-Za-z0-9_-]
        // set and cannot collide with reserved app routes (denylisted in
        // schemas._validate_alias), so the frame can only ever resolve to the
        // upstream service. Many upstreams (e.g. coder) need same-origin
        // cookies/storage to function, so an opaque-origin sandbox would break
        // them. AppManager itself remains unframable (frame-ancestors 'none' +
        // X-Frame-Options: DENY), and the session cookie is HttpOnly.
        sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-downloads"
      />
    </div>
  );
}
