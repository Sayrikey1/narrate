import { useState } from "react";
import { api } from "../api";
import { Notice } from "../ui/feedback";
import { TextField } from "../ui/form";
import { ModelField } from "./ModelField";
import { VoicePicker } from "./VoicePicker";
import type { Model, Project, VoiceSettings } from "../types";

/** Create a project, or change an existing one's voice and delivery.
 *
 *  One component for both, because they are the same form with a different
 *  starting point and a different verb — and when they were two, the create
 *  path grew a monthly cap field that the edit path never got.
 *
 *  Split out of `ProjectBar`, which was 303 lines doing four jobs: listing
 *  projects, this form, the profile editor, and the model select. Each of those
 *  is now its own file.
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
  const [cap, setCap] = useState("");
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
        });
        setMessage({
          tone: "ok",
          text: result.rejected_settings.length
            ? `Saved. ${model?.label} does not honour ${result.rejected_settings.join(", ")} — dropped.`
            : "Saved.",
        });
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
      }
      onDone();
    } catch (e) {
      setMessage({ tone: "error", text: String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="subpanel" onSubmit={submit}>
      {!editing && (
        <TextField
          label="Name"
          value={name}
          onChange={setName}
          placeholder="Ep14"
          disabled={busy}
        />
      )}

      <ModelField
        models={models}
        value={modelId}
        onChange={setModelId}
        autoSelect={!editing}
      />

      <VoicePicker
        model={model}
        voiceId={voiceId}
        settings={settings}
        onVoice={setVoiceId}
        onSettings={setSettings}
        disabled={busy}
      />

      {!editing && (
        <TextField
          label="Monthly cap (USD)"
          value={cap}
          onChange={setCap}
          placeholder="no cap"
          type="number"
          step="0.01"
          min="0"
          disabled={busy}
          hint="Warns at 80%, blocks at 100%. Counts every charge this month, not just this run."
        />
      )}

      <button className="primary" disabled={busy || (!editing && (!name.trim() || !modelId))}>
        {busy ? "Saving…" : editing ? "Save" : "Create project"}
      </button>

      {message && <Notice tone={message.tone}>{message.text}</Notice>}
    </form>
  );
}
