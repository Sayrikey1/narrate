import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Loadable } from "../ui/feedback";
import { DownloadIcon } from "./Icon";
import { splitName, voiceFace } from "./VoicePicker";
import type { Variant, Voice } from "../types";

/** Every voice this script has been performed in, each exportable on its own.
 *
 *  Nothing is stored to make this work. A take has always recorded the voice
 *  that produced it, so generating a script twice — once in the original voice,
 *  once in a clone — leaves both sets of takes side by side, and a variant is
 *  only a different way of choosing between them. Two masters come out of one
 *  generation history.
 *
 *  A voice that covers fewer chunks than the script has cannot be exported: the
 *  master would have a silent hole where the missing lines are. Saying which
 *  lines are missing is more use than greying out a button.
 */
export function Variants({
  scriptId,
  title,
  onExported,
}: {
  scriptId: number;
  /** The episode's title, for the card's subtitle. */
  title?: string;
  onExported?: () => void;
}) {
  const [variants, setVariants] = useState<Variant[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([api.variants(scriptId), api.voices().catch(() => [])])
      .then(([found, allVoices]) => {
        setVariants(found);
        setVoices(allVoices);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [scriptId]);

  useEffect(load, [load]);

  // A voice id is opaque; the name is what a person recognises. Falls back to
  // the id, because a voice deleted from the account still has takes here.
  const voiceOf = (id: string) => voices.find((v) => v.voice_id === id);
  const nameOf = (id: string) => {
    const voice = voiceOf(id);
    return voice ? splitName(voice.name)[0] : id;
  };

  async function exportVariant(variant: Variant) {
    setBusy(variant.voice_id);
    setError(null);
    setDone(null);
    try {
      const result = await api.export(scriptId, undefined, undefined, {
        voice_id: variant.voice_id,
        label: nameOf(variant.voice_id),
      });
      setDone(`${nameOf(variant.voice_id)} → ${result.out_dir.split("/").slice(-1)[0]}`);
      onExported?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel" aria-labelledby="versions-h">
      <div className="card-head">
        <div className="card-head-text">
          <h2 id="versions-h" className="card-title">
            🗣️ Voice versions
          </h2>
          <p className="card-sub">
            {title ? `${title} performed` : "Performed"} in each voice it's been generated in.
          </p>
        </div>
      </div>
      <Loadable loading={loading} error={error} rows={2} onRetry={load}>
        {variants.length === 0 ? (
          <div className="empty">
            <strong>Nothing generated yet</strong>
            Generate the script and the voice it was made in appears here.
          </div>
        ) : (
          <div className="versions">
            {variants.map((v) => {
              const voice = voiceOf(v.voice_id);
              const missing = v.chunks_total - v.chunks;
              return (
                <div key={v.voice_id} className="version">
                  <div className="version-head">
                    <span className="voice-face" aria-hidden>
                      {voice ? voiceFace(voice) : "?"}
                    </span>
                    <span className="version-name">
                      <strong>{nameOf(v.voice_id)}</strong>
                      <span className="mono faint">{v.voice_id.slice(0, 10)}…</span>
                    </span>
                    {v.complete ? (
                      <span className="pill ok">
                        ✓ {v.chunks}/{v.chunks_total}
                      </span>
                    ) : (
                      <span className="pill warn">
                        {v.chunks}/{v.chunks_total} · {missing} missing
                      </span>
                    )}
                  </div>
                  <div className={v.complete ? "meter" : "meter warn"} aria-hidden>
                    <div style={{ width: `${(v.chunks / Math.max(1, v.chunks_total)) * 100}%` }} />
                  </div>
                  {v.complete && (
                    <button
                      className="small version-export"
                      disabled={busy !== null}
                      onClick={() => void exportVariant(v)}
                      title={`Stitch ${nameOf(v.voice_id)}'s performance into its own master`}
                    >
                      <DownloadIcon /> {busy === v.voice_id ? "Exporting…" : "Export this version"}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {done && (
          <p className="note ok flush" role="status">
            Exported {done}
          </p>
        )}
        {variants.length > 0 && (
          <p className="note flush">
            {variants.some((v) => !v.complete)
              ? "A voice that's missing chunks can't be exported — the master would have a silent gap. "
              : ""}
            {variants.length > 1
              ? "Each complete version exports to its own folder, so neither overwrites the other."
              : "For a second version, change the project's voice and generate again with force. The takes you have are kept, and both versions export from here."}
          </p>
        )}
      </Loadable>
    </section>
  );
}
