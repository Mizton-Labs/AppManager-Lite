import { getAppName, getCollaborators, GITHUB_URL } from "../branding";
import { GithubIcon } from "./icons";

/**
 * About page. Shows the application name, a link to the source repository, the
 * build version (with the commit baked in at build time), the branch that
 * build came from, the development team, and any administrator-configured
 * collaborators.
 *
 * The version, commit, branch, and development-team list are injected at
 * build time from package.json and the git commit history (see
 * vite.config.ts). The collaborators are an admin-managed list delivered with
 * the session.
 */
export function AboutView() {
  const version = __APP_VERSION__;
  const commit = __APP_COMMIT__;
  const branch = __APP_BRANCH__;
  const contributors = __APP_CONTRIBUTORS__;
  const collaborators = getCollaborators();

  return (
    <div className="stack wide">
      <header className="view-head">
        <h1>About</h1>
        <p className="muted">Application details and the team behind it.</p>
      </header>

      <section className="card about-card">
        <dl className="detail-list">
          <div className="detail-row">
            <dt>Application</dt>
            <dd>{getAppName()}</dd>
          </div>

          <div className="detail-row">
            <dt>Repository</dt>
            <dd>
              <a
                className="about-github"
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
              >
                <GithubIcon />
                <span>GitHub</span>
              </a>
            </dd>
          </div>

          <div className="detail-row">
            <dt>Version</dt>
            <dd>
              <code>
                {version} ({commit})
              </code>
            </dd>
          </div>

          <div className="detail-row">
            <dt>Branch</dt>
            <dd>
              <code>{branch}</code>
            </dd>
          </div>

          <div className="detail-row">
            <dt>Development team</dt>
            <dd>
              {contributors.length === 0 ? (
                <span className="muted">No contributor metadata available.</span>
              ) : (
                <ul className="about-contributors">
                  {contributors.map((contributor) => (
                    <li key={contributor.handle}>
                      <a
                        className="about-contributor"
                        href={contributor.url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        @{contributor.handle}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </dd>
          </div>

          {collaborators.length > 0 && (
            <div className="detail-row">
              <dt>Collaborators</dt>
              <dd>
                <ul className="about-contributors">
                  {collaborators.map((name) => (
                    <li key={name}>{name}</li>
                  ))}
                </ul>
              </dd>
            </div>
          )}
        </dl>
      </section>
    </div>
  );
}
