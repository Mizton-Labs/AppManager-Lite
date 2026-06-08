import type { ApiUser } from "../types";
import { ChangePasswordForm } from "./ChangePasswordForm";

export function AccountPanel(props: {
  user: ApiUser;
  onPasswordChanged: () => void | Promise<void>;
}) {
  const { user } = props;
  return (
    <div className="grid">
      <section className="card">
        <h2>Profile</h2>
        <dl className="detail-list">
          <div>
            <dt>Username</dt>
            <dd>{user.username}</dd>
          </div>
          <div>
            <dt>Role</dt>
            <dd>
              <span className="role-badge">{user.role}</span>
            </dd>
          </div>
          <div>
            <dt>Teams</dt>
            <dd>
              {user.teams.length > 0 ? (
                <span className="tag-row">
                  {user.teams.map((team) => (
                    <span key={team} className="tag">
                      {team}
                    </span>
                  ))}
                </span>
              ) : (
                <span className="muted">No teams assigned</span>
              )}
            </dd>
          </div>
          <div>
            <dt>Self-service</dt>
            <dd>
              {user.self_service ? (
                <span className="status-badge ok">enabled</span>
              ) : (
                <span className="muted">
                  Disabled &mdash; new applications need administrator approval
                </span>
              )}
            </dd>
          </div>
        </dl>
      </section>

      <section className="card">
        <h2>Change password</h2>
        <ChangePasswordForm onChanged={props.onPasswordChanged} />
      </section>
    </div>
  );
}
