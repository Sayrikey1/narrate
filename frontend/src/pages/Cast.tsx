import { useEffect, useState } from "react";
import { api } from "../api";
import { Audition } from "../components/Audition";
import { PlusIcon, TrashIcon } from "../components/Icon";
import { Empty, Loadable, Notice } from "../ui/feedback";
import { SelectField, TextField } from "../ui/form";
import { Card, PageHeader, Split } from "../ui/layout";
import { DataTable, type Column } from "../ui/Table";
import type { CastMember, ScriptSummary, Voice } from "../types";

/** Who speaks in this project, and in which voice.
 *
 *  The cast is the whole safety mechanism for multi-speaker scripts: a `Morag:`
 *  prefix becomes a speaker change **only** when Morag is cast. An uncast name
 *  stays ordinary narration, which is why the feature can run over existing
 *  writing without quietly editing it — `Note:`, `Warning:` and `12:30` are
 *  never mistaken for dialogue.
 */
export function CastPage({
  projectId,
  projectName,
  scripts,
  onChanged,
}: {
  projectId: number | null;
  projectName: string;
  scripts: ScriptSummary[];
  onChanged: () => void;
}) {
  const [members, setMembers] = useState<CastMember[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [name, setName] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (projectId === null) return;
    setLoading(true);
    Promise.all([api.cast(projectId), api.voices()])
      .then(([cast, allVoices]) => {
        setMembers(cast);
        setVoices(allVoices);
        setVoiceId((current) => current || allVoices[0]?.voice_id || "");
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [projectId]);

  if (projectId === null) {
    return (
      <>
        <PageHeader title="Cast" subtitle="who speaks, and in which voice" />
        <Card>
          <Empty title="Pick a project first">
            The cast belongs to a project, so a character carries across every
            episode in it.
          </Empty>
        </Card>
      </>
    );
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || !voiceId) return;
    try {
      await api.setCastMember(projectId!, { name: name.trim(), voice_id: voiceId });
      setMembers(await api.cast(projectId!));
      setName("");
      setError(null);
      onChanged();
    } catch (e) {
      setError(String(e));
    }
  }

  const nameOf = (id: string) => voices.find((v) => v.voice_id === id)?.name ?? id;

  const columns: readonly Column<CastMember>[] = [
    { header: "Writes as", cell: (m) => <code>{m.name}:</code> },
    {
      header: "Voice",
      cell: (m) => (
        <>
          {nameOf(m.voice_id)} <span className="dim mono">{m.voice_id}</span>
        </>
      ),
    },
    {
      header: "",
      cell: (m) => (
        <Audition src={api.voicePreview(m.voice_id)} label={`${m.name}'s voice`} />
      ),
    },
    {
      header: "",
      align: "num",
      cell: (m) => (
        <button
          className="ghost small danger"
          title={`Uncast ${m.name}`}
          aria-label={`Uncast ${m.name}`}
          onClick={async () => {
            await api.removeCastMember(projectId!, m.id);
            setMembers(await api.cast(projectId!));
            onChanged();
          }}
        >
          <TrashIcon />
        </button>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Cast"
        subtitle={`${projectName} — ${
          members.length
            ? `${members.length} speaker${members.length === 1 ? "" : "s"}`
            : "who speaks, and in which voice"
        }`}
      />

      <Split
        main={
          <Card title="Speakers">
            <Loadable loading={loading} error={error} rows={3}>
              <DataTable
                columns={columns}
                rows={members}
                rowKey={(m) => m.id}
                caption="Cast members and their voices"
                empty={{
                  title: "Nobody is cast yet",
                  body: (
                    <>
                      Add a name below, then write <code>Morag: her line</code> in a
                      script.
                    </>
                  ),
                }}
              />
            </Loadable>

            <form className="cast-add" onSubmit={save}>
              <TextField
                label="Name in the script"
                value={name}
                onChange={setName}
                placeholder="Morag"
              />
              <SelectField
                label="Voice"
                value={voiceId}
                onChange={setVoiceId}
                options={voices.map((voice) => ({
                  value: voice.voice_id,
                  label: `${voice.name}${voice.mock ? " (offline stand-in)" : ""}`,
                }))}
              />
              <button className="primary" disabled={!name.trim() || !voiceId}>
                <PlusIcon /> Cast
              </button>
            </form>
          </Card>
        }
        aside={
          <>
            <Card title="Writing dialogue">
              <p className="md-p">
                Put the name before a colon and the line belongs to that voice:
              </p>
              <pre className="md-code">
                {`Morag: You knew it would end.\nKeeper: I hoped not.`}
              </pre>
              <Notice>
                A prefix only counts when the name is cast. <code>Note:</code> and{" "}
                <code>12:30</code> stay narration, so this never rewrites prose
                that happens to contain a colon.
              </Notice>
              <p className="md-p">
                A script can also cast its own one-off characters, which override
                this list:
              </p>
              <pre className="md-code">{`[CAST] Morag = <voice_id>`}</pre>
              <Notice>
                <code>[VOICE: Morag]</code> switches speaker mid-paragraph.
              </Notice>
            </Card>

            {members.length > 0 && (
              <Card title="Applying a change">
                <Notice tone="warn">
                  A chunk keeps the voice it was ingested with, so recasting does
                  not silently reassign audio that is already generated and
                  already paid for. Re-ingest a script to pick this up.
                </Notice>
                {scripts.length > 0 && (
                  <p className="dim">
                    {scripts.length} script{scripts.length === 1 ? "" : "s"} in this
                    project.
                  </p>
                )}
              </Card>
            )}
          </>
        }
      />
    </>
  );
}
