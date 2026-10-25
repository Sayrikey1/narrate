import { useState } from "react";
import { api } from "../api";
import { spoken } from "../findings";
import * as transport from "../transport";
import type { ChunkRow, Slot, Timeline } from "../types";
import { Audition } from "./Audition";
import { TrashIcon } from "./Icon";

/** Where the episode's effects sit over its narration: one block per chunk,
 *  a pin where each effect starts. A pin plays the episode from there.
 *
 *  Effects never add time — they play over the speech — so the pins sit on
 *  the narration's own scale rather than between its blocks. */
export function WhereTheySit({ timeline }: { timeline: Timeline }) {
  const narration = timeline.entries.filter((e) => e.kind === "narration");
  const cues = timeline.entries.filter((e) => e.kind !== "narration");
  const end = Math.max(1, ...timeline.entries.map((e) => e.end_s));
  const at = (seconds: number) => `${(seconds / end) * 100}%`;
  // Effects placed at the same moment would cover each other; each further one
  // steps to the right of the last.
  const seen = new Map<number, number>();

  return (
    <div className="fx-track">
      <div className="fx-track-chunks" aria-hidden>
        {narration.map((e) => (
          <span
            key={e.index}
            className={e.generated ? "fx-track-chunk" : "fx-track-chunk missing"}
            style={{ left: at(e.start_s), width: `calc(${at(e.end_s - e.start_s)} - 4px)` }}
          >
            {e.chunk_ordinal}
          </span>
        ))}
      </div>
      {cues.map((e) => {
        const step = seen.get(e.start_s) ?? 0;
        seen.set(e.start_s, step + 1);
        const label = `${e.label} at ${clock(e.start_s)}${e.generated ? " — play from here" : " — planned, not generated"}`;
        return (
          <span key={e.index}>
            <span className="fx-pin-stem" style={{ left: at(e.start_s) }} aria-hidden />
            <button
              type="button"
              className={e.generated ? "fx-pin" : "fx-pin planned"}
              style={{ left: `calc(${at(e.start_s)} + ${step * 30}px)` }}
              aria-label={label}
              title={label}
              onClick={() => {
                transport.seek(e.start_s);
                transport.play();
              }}
            >
              {e.generated ? "✨" : "◌"}
            </button>
          </span>
        );
      })}
    </div>
  );
}

/** One placement: what it is, where it sits and the words it plays over, and
 *  whether it has a sound yet. Removing it is asked in place. */
export function FxCard({
  slot,
  reused,
  others,
  timeline,
  chunk,
  onChange,
}: {
  slot: Slot;
  /** An earlier placement already paid for this sound. */
  reused: boolean;
  /** Other placements of the same sound, kept when this one goes. */
  others: number;
  timeline: Timeline | null;
  chunk: ChunkRow | undefined;
  onChange: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const suggestion = !slot.accepted;
  const planned = slot.accepted && !slot.effect_id;
  const starts = timeline?.entries.find((e) => e.slot_id === slot.id)?.start_s;

  async function act(action: () => Promise<unknown>) {
    setWorking(true);
    setError(null);
    try {
      await action();
      onChange();
    } catch (e) {
      setError(String(e));
    } finally {
      setWorking(false);
    }
  }

  return (
    <article
      className={["fx-card", planned ? "planned" : "", suggestion ? "suggested" : ""].filter(Boolean).join(" ")}
      aria-label={slot.description}
    >
      <div className="fx-card-main">
        <span className={slot.effect_id ? "fx-tile" : "fx-tile empty"} aria-hidden>
          {suggestion ? "🪄" : "✨"}
        </span>
        <div className="fx-card-text">
          <div className="fx-card-title">
            <strong>{slot.description}</strong>
            <span className="mono faint">
              {slot.duration_s ? `${slot.duration_s} s` : "auto length"}
              {slot.loop ? " · loop" : ""} · {SOURCE[slot.source](slot.accepted)}
            </span>
          </div>
          <div className="fx-card-where">
            <span className="mono chunk-chip">
              Chunk {slot.at_chunk_ordinal}
              {starts !== undefined ? ` · ${clock(starts)}` : ""}
            </span>
            {chunk && <span className="passage">“{excerpt(chunk.text)}”</span>}
          </div>
          {slot.note && !suggestion && <span className="fx-card-note">{slot.note}</span>}
        </div>
        <div className="fx-card-side">
          {slot.effect_id && (
            <span className="fx-sound">
              <Audition src={api.audioEffect(slot.effect_id)} label={slot.description} />
              {slot.slug && (
                <span className="mono" title={slot.slug}>
                  {slot.slug}
                </span>
              )}
            </span>
          )}
          {reused && <span className="pill ok">♻️ reused · free</span>}
          {planned && <span className="pill planned">planned · not generated</span>}
          {suggestion && <span className="pill quiet">🪄 not accepted yet</span>}
          {!suggestion && (
            <button
              type="button"
              className="icon-button danger"
              aria-label={`Remove ${slot.description}`}
              title="Remove"
              aria-expanded={confirming}
              disabled={working}
              onClick={() => setConfirming((v) => !v)}
            >
              <TrashIcon size={15} />
            </button>
          )}
        </div>
      </div>

      {suggestion && (
        <div className="fx-suggest">
          <span>
            {slot.note ? `${slot.note} ` : ""}Accepting is free — it's priced and generated with the
            next Generate.
          </span>
          <button type="button" className="small" disabled={working} onClick={() => void act(() => api.deleteSlot(slot.id))}>
            Dismiss
          </button>
          <button
            type="button"
            className="small primary"
            disabled={working}
            onClick={() => void act(() => api.acceptSlot(slot.id, true))}
          >
            Accept
          </button>
        </div>
      )}

      {confirming && (
        <div className="confirm" role="alertdialog" aria-label="Remove this placement?">
          <span>
            <strong>Remove this placement?</strong>{" "}
            <span className="dim">
              {slot.effect_id
                ? `The sound stays in the library${others ? ` and keeps its other placement${others === 1 ? "" : "s"}` : ""}. Removing is free.`
                : "Nothing has been generated for it, so removing is free."}
            </span>
          </span>
          <button type="button" className="small" autoFocus onClick={() => setConfirming(false)}>
            Keep it
          </button>
          <button
            type="button"
            className="small solid-danger"
            disabled={working}
            onClick={() => void act(() => api.deleteSlot(slot.id))}
          >
            Remove
          </button>
        </div>
      )}
      {error && <p className="note error flush">{error}</p>}
    </article>
  );
}

const SOURCE: Record<Slot["source"], (accepted: boolean) => string> = {
  marker: () => "from a marker",
  manual: () => "added by hand",
  suggested: (accepted) => (accepted ? "suggested, accepted" : "suggested"),
};

/** Place an effect at the start of a chunk. Free: nothing is generated until
 *  the next Generate, which prices it first. */
export function AddEffect({
  scriptId,
  chunks,
  onChange,
}: {
  scriptId: number;
  chunks: ChunkRow[];
  onChange: () => void;
}) {
  const [description, setDescription] = useState("");
  const [chunk, setChunk] = useState(1);
  const [duration, setDuration] = useState("");
  const [loop, setLoop] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const where = chunks.find((c) => c.ordinal === chunk);

  return (
    <section className="panel" aria-labelledby="add-fx-h">
      <div className="card-head">
        <h2 id="add-fx-h" className="card-title">
          ➕ Add an effect
        </h2>
      </div>
      <form
        className="fx-add"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!description.trim()) return;
          setBusy(true);
          setError(null);
          try {
            await api.addSlot(scriptId, {
              at_chunk_ordinal: chunk,
              description: description.trim(),
              duration_s: duration ? Number(duration) : null,
              loop,
            });
            setDescription("");
            setDuration("");
            setLoop(false);
            onChange();
          } catch (e) {
            setError(String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          <span>Description</span>
          <input
            value={description}
            placeholder="heavy wooden door creaking open"
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label>
          <span>Where</span>
          <select value={chunk} onChange={(e) => setChunk(Number(e.target.value))}>
            {chunks.map((c) => (
              <option key={c.ordinal} value={c.ordinal}>
                Chunk {c.ordinal} — “{excerpt(c.text, 28)}”
              </option>
            ))}
          </select>
        </label>
        {where && <div className="passage-box">“{excerpt(where.text, 110)}”</div>}
        <label>
          <span>
            Length <span className="label-note">— seconds, or leave it to the generator</span>
          </span>
          <input
            className="mono"
            type="number"
            min="0.5"
            max="30"
            step="0.5"
            placeholder="auto"
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
          />
        </label>
        <label className="checkbox">
          <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} />
          🔁 Loop it under the passage
        </label>
        <button type="submit" className="primary large" disabled={busy || !description.trim()}>
          + Add effect
        </button>
        {error && <p className="note error flush">{error}</p>}
        <span className="field-hint">Adding is free. It's priced and generated with the next Generate.</span>
      </form>
    </section>
  );
}

/** The opening words of a chunk, as heard. */
function excerpt(text: string, length = 90): string {
  const words = spoken(text).replace(/\s+/g, " ");
  return words.length > length ? `${words.slice(0, length).replace(/\s+\S*$/, "")}…` : words;
}

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

