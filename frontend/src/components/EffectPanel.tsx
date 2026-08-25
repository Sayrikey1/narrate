import { useState } from "react";
import type { Slot } from "../types";
import { api, usd } from "../api";
import { PlusIcon, SparkIcon, TrashIcon } from "./Icon";
import { Player } from "./Player";

/** Effect slots. A slot with no audio is *planned* — it has a place on the
 *  timeline and nothing generated, which is a useful state rather than an
 *  incomplete one. */
export function EffectPanel({
  slots,
  scriptId,
  onChange,
  busy,
}: {
  slots: Slot[];
  scriptId: number;
  onChange: () => void;
  busy: boolean;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const planned = slots.filter((s) => s.accepted && !s.effect_id);
  const unaccepted = slots.filter((s) => !s.accepted);

  // Two slots asking for the same cue are one generation and one charge, so a
  // naive "N slots × price" estimate would overstate it.
  const distinctPlanned = new Set(
    planned.map((s) => `${s.description}|${s.duration_s}|${s.loop}`),
  ).size;

  async function generate(dryRun: boolean) {
    setWorking(true);
    setMessage(null);
    try {
      const result = await api.generateEffects(scriptId, dryRun);
      const made = result.outcomes.filter((o) => o.status === "generated").length;
      const reused = result.outcomes.filter((o) => o.status === "reused").length;
      setMessage(
        dryRun
          ? `Would generate ${result.outcomes.filter((o) => o.status === "would_generate").length}` +
            ` for ${usd(result.cost_micros)}${reused ? `, reusing ${reused}` : ""}.`
          : `Generated ${made}${reused ? `, reused ${reused}` : ""} — ${usd(result.cost_micros)}.`,
      );
      onChange();
    } catch (error) {
      setMessage(String(error));
    } finally {
      setWorking(false);
    }
  }

  return (
    <>
      {slots.length === 0 ? (
        <div className="empty">
          <strong>No effect slots</strong>
          Add <code>[SFX: description, 4s]</code> markers to the script, or one below.
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th className="num">Chunk</th>
              <th>Effect</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {slots.map((slot) => (
              <tr key={slot.id}>
                <td className="num dim">{slot.at_chunk_ordinal}</td>
                <td>
                  {slot.description}
                  <div className="dim mono">
                    {slot.duration_s ? `${slot.duration_s}s` : "auto"}
                    {slot.loop ? " · loop" : ""} · {slot.source}
                  </div>
                  {slot.note && <div className="note">{slot.note}</div>}
                </td>
                <td>
                  {!slot.accepted ? (
                    <span className="tag">not accepted</span>
                  ) : slot.effect_id ? (
                    <>
                      <span className="tag effect">
                        <SparkIcon /> {slot.slug}
                      </span>{" "}
                      <Player src={api.audioEffect(slot.effect_id)} duration={slot.duration_s} />
                    </>
                  ) : (
                    <span className="tag planned">planned</span>
                  )}
                </td>
                <td>
                  {!slot.accepted && (
                    <button
                      className="small"
                      disabled={busy || working}
                      onClick={async () => {
                        await api.acceptSlot(slot.id, true);
                        onChange();
                      }}
                    >
                      accept
                    </button>
                  )}{" "}
                  <button
                    className="small danger"
                    disabled={busy || working}
                    onClick={async () => {
                      await api.deleteSlot(slot.id);
                      onChange();
                    }}
                  >
                    <TrashIcon />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="toolbar effect-actions">
        <button disabled={busy || working || !planned.length} onClick={() => generate(true)}>
          Dry run
        </button>
        <button
          className="primary"
          disabled={busy || working || !planned.length}
          onClick={() => generate(false)}
        >
          <SparkIcon /> Generate {distinctPlanned || ""}
        </button>
      </div>

      {planned.length > distinctPlanned && (
        <p className="note">
          {planned.length} slots, {distinctPlanned} distinct cue
          {distinctPlanned === 1 ? "" : "s"} — repeats are generated once and reused.
        </p>
      )}
      {unaccepted.length > 0 && (
        <p className="note">
          {unaccepted.length} suggestion{unaccepted.length === 1 ? "" : "s"} awaiting review.
          Nothing is generated until accepted.
        </p>
      )}
      {message && <p className="note">{message}</p>}

      <AddSlot scriptId={scriptId} onChange={onChange} busy={busy || working} />
    </>
  );
}

function AddSlot({
  scriptId,
  onChange,
  busy,
}: {
  scriptId: number;
  onChange: () => void;
  busy: boolean;
}) {
  const [description, setDescription] = useState("");
  const [chunk, setChunk] = useState(1);
  const [duration, setDuration] = useState("");

  return (
    <form
      className="effect-add"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!description.trim()) return;
        await api.addSlot(scriptId, {
          at_chunk_ordinal: chunk,
          description: description.trim(),
          duration_s: duration ? Number(duration) : null,
          loop: false,
        });
        setDescription("");
        onChange();
      }}
    >
      <div className="grow">
        <label>Add an effect</label>
        <input
          value={description}
          placeholder="heavy wooden door creaking open"
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div className="narrow-field">
        <label>At chunk</label>
        <input type="number" min={1} value={chunk} onChange={(e) => setChunk(+e.target.value)} />
      </div>
      <div className="narrow-field">
        <label>Seconds</label>
        <input
          type="number"
          step="0.5"
          min="0.5"
          max="30"
          placeholder="auto"
          value={duration}
          onChange={(e) => setDuration(e.target.value)}
        />
      </div>
      <button disabled={busy}><PlusIcon /> Add</button>
    </form>
  );
}
