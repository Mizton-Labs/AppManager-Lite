import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { Application } from "../types";
import { resolveIconSrc } from "../lib/links";

/** True when the URL resolves to the portal's own origin (rejected as an
 *  embedded source so a same-origin frame cannot reach the AppManager DOM). */
function isSameOrigin(rawUrl: string): boolean {
  try {
    return new URL(rawUrl, window.location.href).origin === window.location.origin;
  } catch {
    return false;
  }
}

/**
 * Renders an embedded application (url_type === "embedded") inside the portal.
 * The app's configured source URL is loaded in a sandboxed iframe that fills the
 * available content area and grows when the sidebar collapses. A compact top bar
 * shows the app title without taking much space from the content.
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
        } else if (isSameOrigin(match.url)) {
          // Defense-in-depth: never frame our own origin. A same-origin embed
          // combined with the iframe sandbox's allow-same-origin could reach the
          // AppManager DOM. Embedded apps are meant to be separate internal
          // services, so refuse a self-referential source.
          setError("This embedded application has an invalid (same-origin) source.");
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
        src={app.url}
        title={app.name}
        sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-downloads"
      />
    </div>
  );
}
