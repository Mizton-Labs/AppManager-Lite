import type { Application } from "../types";
import { resolveAppHref, resolveIconSrc } from "../lib/links";

/** First letters of up to the first two words, e.g. "Hunt Workbench" -> "HW". */
function monogram(name: string): string {
  const initials = name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0] ?? "")
    .join("");
  return initials.toUpperCase() || "?";
}

function publisherLabel(username: string | null | undefined): string {
  if (!username) return "unknown";
  return username.split("@")[0] || username;
}

/**
 * A single application tile: optional icon (or a generated monogram), the
 * application name, a short description, and publisher metadata. The main card
 * content opens the app in a new tab; owned cards may also render a separate
 * edit link. Local-alias applications resolve to an absolute href against the
 * document base URI.
 */
export function AppCard({ app, editHref }: { app: Application; editHref?: string }) {
  const iconSrc = resolveIconSrc(app.icon_url);
  return (
    <div className="app-card">
      <a
        className="app-card-main"
        href={resolveAppHref(app)}
        target="_blank"
        rel="noopener noreferrer"
      >
        <span className="app-card-icon" aria-hidden="true">
          {iconSrc ? (
            <img src={iconSrc} alt="" width={24} height={24} />
          ) : (
            <span className="app-card-monogram">{monogram(app.name)}</span>
          )}
        </span>
        <span className="app-card-title">{app.name}</span>
        {app.description && (
          <span className="app-card-desc">{app.description}</span>
        )}
        {(app.publisher_team || app.created_by) && (
          <span className="app-card-teams publisher-row">
            {app.publisher_team && (
              <span className="tag publisher-team-tag">Team: {app.publisher_team}</span>
            )}
            {app.created_by && (
              <span className="tag publisher-tag">
                Published by: {publisherLabel(app.created_by)}
              </span>
            )}
          </span>
        )}
      </a>
      {editHref && (
        <a className="app-card-edit" href={editHref}>
          Edit
        </a>
      )}
    </div>
  );
}
