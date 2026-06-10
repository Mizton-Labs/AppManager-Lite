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
 * application name, a short description, and small team-scope badges showing
 * which team(s) the application belongs to. Renders as an external link that
 * opens in a new tab; `rel="noopener noreferrer"` prevents reverse-tabnabbing
 * and referrer leakage to the target. Local-alias applications resolve to an
 * absolute href against the document base URI. The whole card is a link, so the
 * team badges are non-interactive labels rather than buttons.
 */
export function AppCard({ app }: { app: Application }) {
  const iconSrc = resolveIconSrc(app.icon_url);
  return (
    <a
      className="app-card"
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
      {app.teams.length > 0 && (
        <span className="app-card-teams">
          {app.teams.map((team) => (
            <span key={team} className="tag app-card-team">
              {team}
            </span>
          ))}
        </span>
      )}
      {app.created_by && (
        <span className="app-card-teams publisher-row">
          <span className="tag publisher-tag">
            published by {publisherLabel(app.created_by)}
          </span>
        </span>
      )}
    </a>
  );
}
