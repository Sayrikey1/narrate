import { useState } from "react";
import { api, usd } from "../api";
import { Link } from "../router";
import { Notice } from "../ui/feedback";
import { TextField } from "../ui/form";
import { ModelField } from "./ModelField";
import { Delivery, VoiceList } from "./VoicePicker";
import type { Model, Project, VoiceSettings } from "../types";

/** Create a project, or change an existing one's model, voice, delivery and
 *  monthly cap.
 *
 *  One component for both, because they are the same form with a different
 *  starting point and a different verb — and when they were two, the create
 *  path grew a monthly cap field that the edit path never got. Now both have
 *  it, and an empty cap on an existing project removes the cap.
 */
export function ProjectForm({
  models,
  project,
  onDone,
}: {
  models: Model[];
  /** Absent when creating. */
  project?: Project;
  onDone: () => void;
}) {
  const editing = project !== undefined;

  const [name, setName] = useState(project?.name ?? "");
  // Empty until the model list arrives when creating. Seeding from `models` in
  // the initial state would leave it stuck at "" on first render while the
  // select displayed whichever option happened to be first — showing one model
  // and creating another.
  const [modelId, setModelId] = useState(project?.model_id ?? "");
  const [voiceId, setVoiceId] = useState<string | null>(project?.voice_id ?? null);
  const [settings, setSettings] = useState<VoiceSettings>({});
  const [cap, setCap] = useState(
    project?.cap_micros != null ? String(project.cap_micros / 1_000_000) : "",
  );
  const [channelTags, setChannelTags] = useState((project?.default_tags ?? []).join(", "));
  const [footer, setFooter] = useState(project?.description_boilerplate ?? "");
  const [message, setMessage] = useState<{ tone: "error" | "ok"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const model = models.find((m) => m.model_id === modelId);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!editing && (!name.trim() || !modelId)) return;
    setBusy(true);
    setMessage(null);
    try {
      if (editing) {
        const result = await api.updateProject(project.id, {
          model_id: modelId,
          voice_id: voiceId ?? undefined,
          settings,
          // Empty means no cap: zero is how the server is told to remove one.
          monthly_cap_usd: cap.trim() ? Number(cap) : 0,
          default_tags: channelTags.split(","),
          description_boilerplate: footer,
        });
        setMessage({
          tone: "ok",
          text: result.rejected_settings.length
            ? `✓ Saved. ${model?.label} does not honour ${result.rejected_settings.join(", ")}, so those were dropped.`
            : "✓ Saved.",
        });
        onDone();
      } else {
        const created = await api.createProject({
          name: name.trim(),
          model_id: modelId,
          voice_id: voiceId ?? undefined,
          monthly_cap_usd: cap ? Number(cap) : undefined,
        });
        if (Object.keys(settings).length) {
          await api.updateProject(created.id, { settings });
        }
        onDone();
      }
    } catch (e) {
      setMessage({ tone: "error", text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="settings-grid" onSubmit={submit}>
      <div className="settings-col">
        {!editing && (
          <TextField label="Name" value={name} onChange={setName} placeholder="Ep14" disabled={busy} />
        )}

        <ModelField models={models} value={modelId} onChange={setModelId} autoSelect={!editing} />

        {editing && project && modelId && modelId !== project.model_id && (
          <Notice tone="warn">
            The model is part of every request, so chunks already generated on the project's
            model — in any script without a model of its own — would be generated and billed again
            on their next run, unless they already have takes on this one. To try another model
            without that, add a new episode on it.
          </Notice>
        )}

        <VoiceList voiceId={voiceId} onVoice={setVoiceId} disabled={busy} />
      </div>

      <div className="settings-col">
        <Delivery model={model} settings={settings} onSettings={setSettings} disabled={busy} />

        <div className="field">
          <label htmlFor={`cap-${project?.id ?? "new"}`}>💳 Monthly cap (USD)</label>
          <div className="input-affix">
            <span className="mono faint" aria-hidden>
              $
            </span>
            <input
              id={`cap-${project?.id ?? "new"}`}
              className="mono"
              type="number"
              step="0.01"
              min="0"
              placeholder="no cap"
              value={cap}
              disabled={busy}
              onChange={(e) => setCap(e.target.value)}
            />
          </div>
          <span className="field-hint">
            Warns at 80%, blocks at 100%. Counts every charge this month, not just one run
            {project?.cap_micros != null
              ? ` — ${usd(project.month_micros, 2)} so far, ${project.cap_used_pct}% of ${usd(project.cap_micros, 2)}.`
              : "."}
            {editing && " Empty removes the cap."}
          </span>
        </div>

        {editing && (
          <fieldset className="dashed-note channel-defaults">
            <legend>🏷️ Channel defaults</legend>
            <span>Go into every episode's publish pack, after the episode's own.</span>
            <label>
              <span>Default tags</span>
              <input
                value={channelTags}
                placeholder="history, business, documentary"
                disabled={busy}
                onChange={(e) => setChannelTags(e.target.value)}
              />
            </label>
            <label>
              <span>Description footer</span>
              <textarea
                rows={3}
                value={footer}
                placeholder="Links and a sign-off that every description ends with."
                disabled={busy}
                onChange={(e) => setFooter(e.target.value)}
              />
            </label>
          </fieldset>
        )}

        <div className="settings-save">
          <button
            type="submit"
            className="primary large"
            disabled={busy || (!editing && (!name.trim() || !modelId))}
          >
            {busy ? "Saving…" : editing ? "Save changes" : "Create project"}
          </button>
          {message && (
            <span role="status" className={message.tone === "ok" ? "ok" : "error"}>
              {message.text}
            </span>
          )}
        </div>
        {!editing && (
          <p className="note flush">
            Cast the speakers for dialogue on the <Link to="/cast">Cast</Link> page once the project
            exists.
          </p>
        )}
      </div>
    </form>
  );
}
