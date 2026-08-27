import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { teamSlug } from "../teams";
import type {
  BundleMappingSource,
  BundleTemplate,
  BundleTemplateMapping,
  ServerTemplateOption,
} from "../types";

type MappingOption = { value: BundleMappingSource; label: string };

const STATIC_MAPPING_OPTIONS: MappingOption[] = [
  { value: "username", label: "Username" },
  { value: "user_id", label: "User ID (derived identifier)" },
  { value: "user_apps_server", label: "Apps server host/IP fallback" },
  { value: "user_apps_server_host", label: "Apps server host" },
  { value: "user_apps_server_ip", label: "Apps server IP" },
  { value: "user_role", label: "User role" },
];

/**
 * Build the mapping-source options from the static sources plus one triple of
 * variables per server template (keyed by the template's slug). Each template
 * variable resolves to the user's first server created from that template.
 */
function buildMappingOptions(
  serverTemplates: ServerTemplateOption[],
): MappingOption[] {
  // Distinct template names can slugify to the same value; keep the first so
  // the dropdown has no duplicate option values/keys.
  const seenSlugs = new Set<string>();
  const dynamic = serverTemplates.flatMap((template) => {
    const slug = teamSlug(template.name);
    if (!slug || seenSlugs.has(slug)) return [];
    seenSlugs.add(slug);
    return [
      {
        value: `server_${slug}_name` as BundleMappingSource,
        label: `${template.name} — name`,
      },
      {
        value: `server_${slug}_ip` as BundleMappingSource,
        label: `${template.name} — IP`,
      },
      {
        value: `server_${slug}_user` as BundleMappingSource,
        label: `${template.name} — user`,
      },
    ];
  });
  return [...STATIC_MAPPING_OPTIONS, ...dynamic];
}

function emptyMapping(): BundleTemplateMapping {
  return { field_name: "", source: "username" };
}

export function BundleTemplateManagement() {
  const [templates, setTemplates] = useState<BundleTemplate[]>([]);
  const [serverTemplates, setServerTemplates] = useState<
    ServerTemplateOption[]
  >([]);
  const [editing, setEditing] = useState<BundleTemplate | null>(null);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [description, setDescription] = useState("");
  const [mappings, setMappings] = useState<BundleTemplateMapping[]>([
    emptyMapping(),
  ]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewingDefinitionId, setViewingDefinitionId] = useState<number | null>(
    null,
  );

  const mappingOptions = useMemo(
    () => buildMappingOptions(serverTemplates),
    [serverTemplates],
  );

  async function reload() {
    const [bundles, servers] = await Promise.all([
      api.listBundleTemplates(),
      api.listServerTemplates(),
    ]);
    setTemplates(bundles);
    setServerTemplates(servers);
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
    setDescription("");
    setMappings([emptyMapping()]);
  }

  function editTemplate(template: BundleTemplate) {
    setEditing(template);
    setName(template.name);
    setContent(template.content);
    setDescription(template.description);
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
          description: description.trim(),
          mappings: cleanedMappings,
        });
      } else {
        await api.createBundleTemplate({
          name: name.trim(),
          content,
          description: description.trim(),
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

  async function cloneTemplate(template: BundleTemplate) {
    setError(null);
    setBusy(true);
    try {
      await api.cloneBundleTemplate(template.id, `${template.name} (copy)`);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to clone template.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(template: BundleTemplate) {
    setError(null);
    setBusy(true);
    try {
      await api.setBundleTemplateEnabled(template.id, !template.enabled);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update template.");
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
          <span>Description (shown to users under the download menu)</span>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What this bundle is for"
            maxLength={500}
          />
        </label>
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
                    {mappingOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                    {/* Preserve a stale/deleted-template source so editing an
                        existing mapping does not silently drop its value. */}
                    {!mappingOptions.some((o) => o.value === mapping.source) && (
                      <option value={mapping.source}>
                        {mapping.source} (unknown template)
                      </option>
                    )}
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
                  {template.is_builtin && (
                    <span className="role-badge">built-in</span>
                  )}
                  <span className="status-badge ok">
                    {template.mappings.length} mappings
                  </span>
                  {!template.enabled && (
                    <span className="status-badge warn">disabled</span>
                  )}
                </div>
                <div className="row-actions">
                  {template.is_builtin ? (
                    <>
                      <button
                        type="button"
                        className="btn ghost"
                        onClick={() =>
                          setViewingDefinitionId(
                            viewingDefinitionId === template.id
                              ? null
                              : template.id,
                          )
                        }
                      >
                        {viewingDefinitionId === template.id
                          ? "Hide definition"
                          : "View definition"}
                      </button>
                      <button
                        type="button"
                        className="btn ghost"
                        onClick={() => cloneTemplate(template)}
                        disabled={busy}
                      >
                        Clone
                      </button>
                      <button
                        type="button"
                        className="btn ghost"
                        onClick={() => toggleEnabled(template)}
                        disabled={busy}
                      >
                        {template.enabled ? "Disable" : "Enable"}
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="btn ghost"
                        onClick={() => editTemplate(template)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className="btn ghost"
                        onClick={() => cloneTemplate(template)}
                        disabled={busy}
                      >
                        Clone
                      </button>
                      <button
                        type="button"
                        className="btn danger"
                        onClick={() => deleteTemplate(template)}
                      >
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </div>
              {template.is_builtin && viewingDefinitionId === template.id && (
                <div className="field">
                  <span className="muted logo-hint">
                    Generic preview of what this built-in template downloads
                    (no real usernames, hosts, or key material). It is rendered
                    dynamically per download from your actual servers and jump
                    server settings.
                  </span>
                  <textarea
                    className="bundle-definition-preview"
                    readOnly
                    rows={12}
                    value={template.definition}
                  />
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
