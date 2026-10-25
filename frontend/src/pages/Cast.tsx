import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Audition } from "../components/Audition";
import { TrashIcon } from "../components/Icon";
import { initial } from "../components/Rail";
import { describe, splitName, voiceFace, VoiceList } from "../components/VoicePicker";
import { Link } from "../router";
import { Empty, Loadable } from "../ui/feedback";
import { PageHeader } from "../ui/layout";
import type { CastMember, Model, Project, ScriptSummary, Voice } from "../types";

/** Who speaks in this project, and in which voice.
 *
 *  The cast is the whole safety mechanism for multi-speaker scripts: a `Morag:`
 *  prefix becomes a speaker change **only** when Morag is cast. An uncast name
 *  stays ordinary narration, which is why the feature can run over existing
 *  writing without quietly editing it — `Note:`, `Warning:` and `12:30` are
 *  never mistaken for dialogue.
 */
export function CastPage({
  project,
  models,
  voices,
  scripts,
  onChanged,
}: {
  project: Project | null;
  models: Model[];
  /** The account's voices, for names and faces. */
  voices: Voice[];
  /** The project's episodes. */
  scripts: ScriptSummary[];
  onChanged: () => void;
}) {
  const [members, setMembers] = useState<CastMember[]>([]);
  const [name, setName] = useState("");
  const [voiceId, setVoiceId] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const form = useRef<HTMLElement | null>(null);
  const nameInput = useRef<HTMLInputElement | null>(null);
  const projectId = project?.id ?? null;

  useEffect(() => {
    if (projectId === null) return;
    setLoading(true);
    api
      .cast(projectId)
      .then(setMembers)
      .catch((e) => setLoadError(String(e)))
      .finally(() => setLoading(false));
  }, [projectId]);

  if (!project) {
    return (
      <>
        <PageHeader title="Cast" subtitle="Who speaks, and in which voice." />
        <div className="panel">
          <Empty title="Pick a project first">
            The cast belongs to a project, so a character carries across every episode in it.
          </Empty>
        </div>
      </>
    );
  }

  const voiceOf = (id: string | null) => voices.find((v) => v.voice_id === id) ?? null;
  const nameOf = (id: string | null) => {
    const voice = voiceOf(id);
    return voice ? splitName(voice.name)[0] : (id ?? "");
  };
  const existing = members.find((m) => m.name === name.trim());
  const model = models.find((m) => m.model_id === project.model_id);
  const spoken = name.trim() || "Morag";

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setMembers(await api.cast(project!.id));
      onChanged();
      return true;
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ""));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || !voiceId) return;
    const ok = await run(() =>
      // Casting by a name already cast changes that speaker's voice, and keeps
      // their note.
      api.setCastMember(project!.id, { name: name.trim(), voice_id: voiceId, note: existing?.note }),
    );
    if (ok) setName("");
  }

  function changeVoice(member: CastMember) {
    setName(member.name);
    setVoiceId(member.voice_id);
    form.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    nameInput.current?.focus({ preventScroll: true });
  }

  return (
    <>
      <PageHeader
        kicker={`${project.name} · who speaks, and in which voice`}
        title="Cast"
        subtitle={`${
          members.length ? `${members.length} speaker${members.length === 1 ? "" : "s"} plus the narrator` : "Just the narrator so far"
        }. A character carries across every episode in the project.`}
        actions={
          <a className="btn primary large" href="#cast-add">
            🎭 Cast a speaker
          </a>
        }
      />

      <div className="row">
        <div className="grow">
          <section className="section" aria-labelledby="who-h">
            <h2 id="who-h" className="section-title">
              Speakers
            </h2>
            <Loadable loading={loading} error={loadError} rows={2}>
              <div className="speaker-cards">
                <article className="speaker-card narrator">
                  <div className="speaker-head">
                    <span className="speaker-tile" aria-hidden>
                      🎙️
                    </span>
                    <div>
                      <strong className="speaker-name">Narrator</strong>
                      <span className="faint">Everything nobody else says</span>
                    </div>
                  </div>
                  <VoiceRow voice={voiceOf(project.voice_id)} fallback={project.voice_id ?? "No voice set"} />
                  <span className="dim small-print">
                    Set in <Link to="/">project settings</Link>.
                  </span>
                </article>

                {members.map((member) => (
                  <article key={member.id} className="speaker-card">
                    <div className="speaker-head">
                      <span className="speaker-tile" aria-hidden>
                        {initial(member.name)}
                      </span>
                      <div>
                        <code>{member.name}:</code>
                        <span className="faint">{member.note || "Lines written with this name"}</span>
                      </div>
                    </div>
                    <VoiceRow voice={voiceOf(member.voice_id)} fallback={member.voice_id} hear={member.name} />
                    <div className="speaker-actions">
                      <button type="button" className="small" disabled={busy} onClick={() => changeVoice(member)}>
                        Change voice…
                      </button>
                      <button
                        type="button"
                        className="small icon-button danger"
                        aria-label={`Remove ${member.name} from the cast…`}
                        title="Remove from the cast"
                        aria-expanded={confirming === member.id}
                        disabled={busy}
                        onClick={() => setConfirming((c) => (c === member.id ? null : member.id))}
                      >
                        <TrashIcon size={15} />
                      </button>
                    </div>
                    {confirming === member.id && (
                      <div className="confirm stacked" role="alertdialog" aria-label={`Remove ${member.name}?`}>
                        <span>
                          <strong>Remove {member.name}?</strong>{" "}
                          <span className="dim">
                            Lines written as <code>{member.name}:</code> will be read by the narrator in
                            episodes added from now on. Audio already generated doesn't change.
                          </span>
                        </span>
                        <div className="confirm-actions">
                          <button type="button" className="small" autoFocus onClick={() => setConfirming(null)}>
                            Keep
                          </button>
                          <button
                            type="button"
                            className="small solid-danger"
                            disabled={busy}
                            onClick={async () => {
                              if (await run(() => api.removeCastMember(project.id, member.id))) setConfirming(null);
                            }}
                          >
                            Remove
                          </button>
                        </div>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </Loadable>
          </section>

          <section id="cast-add" ref={form} className="panel cast-form" aria-labelledby="add-h">
            <div className="card-head">
              <div className="card-head-text">
                <h2 id="add-h" className="card-title">
                  🎭 {existing ? `Recast ${existing.name}` : "Cast a speaker"}
                </h2>
                <p className="card-sub">
                  Name them as you'll write them in the script, then hear voices before you choose
                  one.
                </p>
              </div>
            </div>
            <form onSubmit={save} className="cast-fields">
              <div className="cast-name">
                <label>
                  <span>Name in the script</span>
                  <input
                    ref={nameInput}
                    value={name}
                    placeholder="Morag"
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <div className="cast-preview">
                  <span>You'll write</span>
                  <code>{spoken}: her line</code>
                </div>
              </div>
              <VoiceList voiceId={voiceId} onVoice={setVoiceId} disabled={busy} grid />
              {error && (
                <p className="note error flush" role="alert">
                  {error}
                </p>
              )}
              <div className="cast-go">
                <button type="submit" className="primary large" disabled={busy || !name.trim() || !voiceId}>
                  {!name.trim() || !voiceId
                    ? "Name a speaker and choose a voice"
                    : existing
                      ? `Recast ${name.trim()} as ${nameOf(voiceId)}`
                      : `Cast ${name.trim()} as ${nameOf(voiceId)}`}
                </button>
                <span className="faint small-print">
                  Casting is free. Episodes added or replaced from now on pick it up.
                </span>
              </div>
            </form>
          </section>
        </div>

        <div className="side">
          <section className="panel" aria-labelledby="write-h">
            <div className="card-head">
              <h2 id="write-h" className="card-title">
                ✍️ Writing dialogue
              </h2>
            </div>
            <div className="dialogue-sample mono">
              <span>The phone rang twice before he answered.</span>
              <span>
                <span className="speaker-a">Morag:</span> You knew it would end.
              </span>
              <span>
                <span className="speaker-b">Keeper:</span> I hoped not.
              </span>
              <span className="faint">Note: this line stays with the narrator.</span>
            </div>
            <ul className="dialogue-rules">
              <li>
                A name only counts once it's cast, so <code>Note:</code> and <code>12:30</code> stay
                narration.
              </li>
              <li>
                Cast someone for one episode only with <code>[CAST] Morag = voice_id</code>.
              </li>
              <li>
                Switch speaker mid-paragraph with <code>[VOICE: Morag]</code>.
              </li>
            </ul>
          </section>

          {model?.dialogue && (
            <section className="panel tone-ok" aria-labelledby="dia-h">
              <h2 id="dia-h" className="card-title small">
                💬 Real dialogue
              </h2>
              <p>
                {model.label} can voice several speakers in one request, so they react to each other
                instead of taking turns. Add the episode with{" "}
                <code>narrate script add … --dialogue</code>.
              </p>
            </section>
          )}

          <section className="panel tone-warn" aria-labelledby="apply-h">
            <h2 id="apply-h" className="card-title small">
              🔁 Applying a change
            </h2>
            <p>
              Recasting doesn't change audio that's already generated — and paid for. An episode
              picks up the new cast when its script is replaced
              {scripts.length ? ` — ${scripts.length} episode${scripts.length === 1 ? "" : "s"} in ${project.name}` : ""}.
            </p>
          </section>
        </div>
      </div>
    </>
  );
}

function VoiceRow({ voice, fallback, hear }: { voice: Voice | null; fallback: string; hear?: string }) {
  return (
    <div className="voice-row">
      <span className="voice-row-face" aria-hidden>
        {voice ? voiceFace(voice) : "?"}
      </span>
      <span className="voice-row-text">
        <strong>{voice ? splitName(voice.name)[0] : fallback}</strong>
        {voice && <span>{describe(voice)}</span>}
      </span>
      {voice?.preview && hear && (
        <Audition src={api.voicePreview(voice.voice_id)} label={`${hear}'s voice`} />
      )}
    </div>
  );
}

