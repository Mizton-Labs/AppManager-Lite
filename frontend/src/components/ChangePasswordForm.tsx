import { useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { passwordIssues } from "../lib/password";

export function ChangePasswordForm(props: {
  requireCurrent?: boolean;
  submitLabel?: string;
  onChanged: () => void | Promise<void>;
}) {
  const requireCurrent = props.requireCurrent ?? true;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const issues = useMemo(() => (next ? passwordIssues(next) : []), [next]);
  const mismatch = confirm.length > 0 && next !== confirm;
  const canSubmit =
    !busy &&
    (!requireCurrent || current.length > 0) &&
    next.length > 0 &&
    issues.length === 0 &&
    !mismatch;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    setBusy(true);
    try {
      await api.changeOwnPassword(current, next, confirm);
      setSuccess("Password updated.");
      setCurrent("");
      setNext("");
      setConfirm("");
      await props.onChanged();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Unable to update password. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={onSubmit}>
      {requireCurrent && (
        <label className="field">
          <span>Current password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            required
          />
        </label>
      )}

      <label className="field">
        <span>New password</span>
        <input
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
        />
      </label>

      {issues.length > 0 && (
        <ul className="hint-list" aria-live="polite">
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}

      <label className="field">
        <span>Confirm new password</span>
        <input
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
        />
      </label>

      {mismatch && <p className="hint">Passwords do not match.</p>}
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      {success && (
        <p className="alert success" role="status">
          {success}
        </p>
      )}

      <button type="submit" className="btn primary" disabled={!canSubmit}>
        {busy ? "Saving…" : props.submitLabel ?? "Change password"}
      </button>
    </form>
  );
}
