import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type {
  BundleMappingSource,
  BundleTemplate,
  BundleTemplateMapping,
} from "../types";

const MAPPING_OPTIONS: { value: BundleMappingSource; label: string }[] = [
  { value: "username", label: "Username" },
  { value: "user_apps_server", label: "User apps server" },
  { value: "user_role", label: "User role" },
];

function emptyMapping(): BundleTemplateMapping {
  return { field_name: "", source: "username" };
}

export function BundleTemplateManagement() {
  const [templates, setTemplates] = useState<BundleTemplate[]>([]);
  const [editing, setEditing] = useState<BundleTemplate | null>(null);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [mappings, setMappings] = useState<BundleTemplateMapping[]>([
    emptyMapping(),
  ]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reload() {
    setTemplates(await api.listBundleTemplates());
  }

  useEffect(() => {
    reload()
      .catch((err) =>
        setError(
          err instanceof ApiError ? err.message : "Failed to load bundle templates.",
        ),
      )
      .finally(() => setLoading(false));
  }, []);

  function resetForm() {
    setEditing(null);
    setName("");
    setContent("");
    setMappings([emptyMapping()]);
  }

  function editTemplate(template: BundleTemplate) {
    setEditing(template);
    setName(template.name);
    setContent(template.content);
    setMappings(template.mappings.length > 0 ? template.mappings : [emptyMapping()]);
  }

  function updateMapping(index: number, patch: Partial<BundleTemplateMapping>) {
    setMappings((current) =>
      current.map((mapping, i) => (i === index ? { ...mapping, ...patch } : mapping)),
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    const cleanedMappings = mappings.filter((mapping) => mapping.field_name.trim());
    try {
      if (editing) {
        await api.updateBundleTemplate(editing.id, {
          name: name.trim(),
          content,
          mappings: cleanedMappings,
        });
      } else {
        await api.createBundleTemplate({
          name: name.trim(),
          content,
          mappings: cleanedMappings,
        });
      }
      resetForm();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to save template.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteTemplate(template: BundleTemplate) {
    setError(null);
    setBusy(true);
    try {
      await api.deleteBundleTemplate(template.id);
      if (editing?.id === template.id) resetForm();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to delete template.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Bundle Templates</h2>
      <p className="muted">
        Define downloadable user configuration bundles. Template fields are
        replaced with the selected user values when a user downloads a bundle.
      </p>
      {error && (
        <p className="alert error" role="alert">
          {error}
        </p>
      )}
      <form className="create-form" onSubmit={submit}>
        <div className="form-row">
          <label className="field">
            <span>Template name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Shell profile"
              required
            />
          </label>
        </div>
        <label className="field">
          <span>Template content</span>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="USER runs on APPS_SERVER"
            rows={5}
            required
          />
        </label>
        <fieldset className="team-picker bundle-mapping-picker">
          <legend>Field mappings</legend>
          <div className="stack compact">
            {mappings.map((mapping, index) => (
              <div className="bundle-mapping-row" key={index}>
                <label className="field">
                  <span>Template field</span>
                  <input
                    type="text"
                    value={mapping.field_name}
                    onChange={(e) => updateMapping(index, { field_name: e.target.value })}
                    placeholder="USER"
                  />
                </label>
                <label className="field">
                  <span>Value</span>
                  <select
                    value={mapping.source}
                    onChange={(e) =>
                      updateMapping(index, {
                        source: e.target.value as BundleMappingSource,
                      })
                    }
                  >
                    {MAPPING_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() =>
                    setMappings((current) => current.filter((_, i) => i !== index))
                  }
                  disabled={mappings.length === 1}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="btn ghost"
            onClick={() => setMappings((current) => [...current, emptyMapping()])}
          >
            Add mapping
          </button>
        </fieldset>
        <div className="row-actions">
          <button
            type="submit"
            className="btn primary"
            disabled={busy || !name.trim() || !content.trim()}
          >
            {busy ? "Saving..." : editing ? "Save template" : "Add template"}
          </button>
          {editing && (
            <button type="button" className="btn ghost" onClick={resetForm}>
              Cancel edit
            </button>
          )}
        </div>
      </form>
      {loading ? (
        <p role="status">Loading bundle templates...</p>
      ) : templates.length === 0 ? (
        <p className="muted">No bundle templates yet.</p>
      ) : (
        <div className="user-list bundle-template-list">
          {templates.map((template) => (
            <article className="user-card" key={template.id}>
              <div className="user-card-head">
                <div className="user-identity">
                  <span className="user-name">{template.name}</span>
                  <span className="status-badge ok">
                    {template.mappings.length} mappings
                  </span>
                </div>
                <div className="row-actions">
                  <button
                    type="button"
                    className="btn ghost"
                    onClick={() => editTemplate(template)}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="btn danger"
                    onClick={() => deleteTemplate(template)}
                  >
                    Delete
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
