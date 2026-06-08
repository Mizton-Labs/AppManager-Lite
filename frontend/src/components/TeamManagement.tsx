import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Team } from "../types";
import { TEAM_ICON_CATALOGUE } from "../teamIcons";
import { fileToLogoDataUrl } from "../lib/image";
import { TeamIcon } from "./TeamIcon";
import { PlusIcon } from "./icons";

/**
 * Team management (administrators only).
 *
 * Teams are listed in sidebar order and can be created, renamed, given an icon,
 * reordered (drag-and-drop, with accessible move up/down buttons), and deleted.
 * The icon is chosen from a bundled generic-IT catalogue or uploaded as a small
 * raster image; leaving it unset shows a neutral default on the sidebar.
 */
export function TeamManagement(props: {
  onTeamsChanged?: () => void | Promise<void>;
}) {
  const [teams, setTeams] = useState<Team[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function reload(): Promise<void> {
    setLoading(true);
    try {
      setTeams(await api.listTeams());
      setError(null);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load teams.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function afterChange(next?: Team[]): Promise<void> {
    if (next) setTeams(next);
    else await reload();
    await props.onTeamsChanged?.();
  }

  async function move(index: number, delta: number): Promise<void> {
    const target = index + delta;
    if (target < 0 || target >= teams.length) return;
    const order = teams.map((t) => t.id);
    const [moved] = order.splice(index, 1);
    order.splice(target, 0, moved);
    try {
      const next = await api.reorderTeams(order);
      await afterChange(next);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to reorder teams.",
      );
    }
  }

  return (
    <div className="stack">
      <CreateTeamCard onCreated={() => afterChange()} />

      <section className="card">
        <h2>Teams</h2>
        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}
        {loading ? (
          <p className="muted">Loading teams…</p>
        ) : teams.length === 0 ? (
          <p className="muted">
            No teams yet. Create one above; it will appear in the sidebar.
          </p>
        ) : (
          <ol className="team-list" aria-label="Teams">
            {teams.map((team, index) => (
              <TeamRow
                key={team.id}
                team={team}
                index={index}
                count={teams.length}
                onMove={move}
                onChanged={() => afterChange()}
                onError={setError}
              />
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

function CreateTeamCard(props: { onCreated: () => void | Promise<void> }) {
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createTeam({ name: name.trim(), icon });
      setName("");
      setIcon("");
      await props.onCreated();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to create team.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Add a team</h2>
      <form className="create-form" onSubmit={onSubmit}>
        <label className="field">
          <span>Team name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={40}
            placeholder="e.g. Platform Engineering"
            required
          />
        </label>

        <TeamIconPicker icon={icon} onChange={setIcon} />

        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}

        <div className="row-actions">
          <button
            type="submit"
            className="btn primary"
            disabled={busy || !name.trim()}
          >
            <PlusIcon />
            <span className="btn-label">{busy ? "Adding…" : "Add team"}</span>
          </button>
        </div>
      </form>
    </section>
  );
}

function TeamRow(props: {
  team: Team;
  index: number;
  count: number;
  onMove: (index: number, delta: number) => void | Promise<void>;
  onChanged: () => void | Promise<void>;
  onError: (message: string) => void;
}) {
  const { team, index, count } = props;
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(team.name);
  const [icon, setIcon] = useState(team.icon);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setName(team.name);
    setIcon(team.icon);
  }, [team.name, team.icon]);

  async function save(): Promise<void> {
    setBusy(true);
    try {
      await api.updateTeam(team.id, { name: name.trim(), icon });
      setEditing(false);
      await props.onChanged();
    } catch (err) {
      props.onError(
        err instanceof ApiError ? err.message : "Failed to update team.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    if (
      !window.confirm(
        `Delete team "${team.name}"? Applications and users will lose this team.`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      await api.deleteTeam(team.id);
      await props.onChanged();
    } catch (err) {
      props.onError(
        err instanceof ApiError ? err.message : "Failed to delete team.",
      );
    } finally {
      setBusy(false);
    }
  }

  function onDragStart(event: React.DragEvent): void {
    event.dataTransfer.setData("text/plain", String(index));
    event.dataTransfer.effectAllowed = "move";
  }

  function onDrop(event: React.DragEvent): void {
    event.preventDefault();
    const from = Number(event.dataTransfer.getData("text/plain"));
    if (Number.isInteger(from) && from !== index) {
      void props.onMove(from, index - from);
    }
  }

  return (
    <li
      className="team-list-item"
      draggable={!editing}
      onDragStart={onDragStart}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      <span className="team-drag-handle" aria-hidden="true" title="Drag to reorder">
        ⠿
      </span>
      <span className="team-list-icon" aria-hidden="true">
        <TeamIcon icon={editing ? icon : team.icon} size={22} />
      </span>

      {editing ? (
        <div className="team-edit">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={40}
            aria-label={`Team name for ${team.name}`}
          />
          <TeamIconPicker icon={icon} onChange={setIcon} compact />
        </div>
      ) : (
        <span className="team-list-name">{team.name}</span>
      )}

      <div className="team-row-actions">
        <button
          type="button"
          className="btn ghost btn-sm"
          onClick={() => props.onMove(index, -1)}
          disabled={index === 0 || busy}
          aria-label={`Move ${team.name} up`}
          title="Move up"
        >
          ↑
        </button>
        <button
          type="button"
          className="btn ghost btn-sm"
          onClick={() => props.onMove(index, 1)}
          disabled={index === count - 1 || busy}
          aria-label={`Move ${team.name} down`}
          title="Move down"
        >
          ↓
        </button>
        {editing ? (
          <>
            <button
              type="button"
              className="btn primary btn-sm"
              onClick={save}
              disabled={busy || !name.trim()}
            >
              {busy ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              className="btn ghost btn-sm"
              onClick={() => {
                setEditing(false);
                setName(team.name);
                setIcon(team.icon);
              }}
              disabled={busy}
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="btn ghost btn-sm"
              onClick={() => setEditing(true)}
              disabled={busy}
            >
              Edit
            </button>
            <button
              type="button"
              className="btn danger btn-sm"
              onClick={remove}
              disabled={busy}
            >
              Delete
            </button>
          </>
        )}
      </div>
    </li>
  );
}

/**
 * Icon picker: a grid of bundled catalogue icons plus an upload control and a
 * "use default" option. The selected value is a relative catalogue path or a
 * raster data URI (uploaded), or empty for the neutral default.
 */
function TeamIconPicker(props: {
  icon: string;
  onChange: (value: string) => void;
  compact?: boolean;
}) {
  const { icon } = props;
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  async function onFile(event: React.ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      props.onChange(await fileToLogoDataUrl(file));
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  const isCustom = icon.startsWith("data:");

  return (
    <fieldset className={compactClass("team-icon-picker", props.compact)}>
      <legend>Icon</legend>
      <div className="team-icon-grid" role="radiogroup" aria-label="Team icon">
        <button
          type="button"
          className={!icon ? "team-icon-choice selected" : "team-icon-choice"}
          aria-pressed={!icon}
          onClick={() => props.onChange("")}
          title="Default icon"
        >
          <span className="team-icon-default" aria-hidden="true">
            ★
          </span>
          <span className="team-icon-label">Default</span>
        </button>
        {TEAM_ICON_CATALOGUE.map((option) => (
          <button
            key={option.id}
            type="button"
            className={
              icon === option.path
                ? "team-icon-choice selected"
                : "team-icon-choice"
            }
            aria-pressed={icon === option.path}
            onClick={() => props.onChange(option.path)}
            title={option.label}
          >
            <TeamIcon icon={option.path} size={22} />
            <span className="team-icon-label">{option.label}</span>
          </button>
        ))}
      </div>

      <div className="team-icon-upload">
        <input
          type="file"
          accept="image/png,image/webp,image/jpeg"
          onChange={onFile}
          aria-label="Upload custom team icon"
        />
        {isCustom && (
          <span className="team-icon-custom">
            <TeamIcon icon={icon} size={22} />
            Custom icon selected
          </span>
        )}
        {uploading && <span className="muted">Processing…</span>}
      </div>
      {uploadError && (
        <p className="alert error" role="alert">
          {uploadError}
        </p>
      )}
    </fieldset>
  );
}

function compactClass(base: string, compact?: boolean): string {
  return compact ? `${base} compact` : base;
}
