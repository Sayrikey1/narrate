import { useEffect, useState } from "react";
import { api, usd } from "../api";
import { AddEffect, FxCard, WhereTheySit } from "../components/EffectPanel";
import { PageHeader } from "../ui/layout";
import type { ChunkRow, ScriptSummary, Slot, Timeline } from "../types";

/** An episode's sound design: where each effect sits, what it plays over, and
 *  what is still to generate — priced before anything is spent. */
export function EffectsPage({
  scriptId,
  script,
  slots,
  chunks,
  timeline,
  onRefresh,
}: {
  scriptId: number;
  script: ScriptSummary | null;
  slots: Slot[];
  chunks: ChunkRow[];
  timeline: Timeline | null;
  onRefresh: () => void;
}) {
  const accepted = slots.filter((s) => s.accepted);
  const planned = accepted.filter((s) => !s.effect_id);
  const suggestions = slots.filter((s) => !s.accepted);
  // Two placements asking for the same cue are one generation and one charge.
  const sounds = new Set(accepted.map((s) => s.effect_id ?? `${s.description}|${s.duration_s}|${s.loop}`)).size;
  const { quote, failed } = useQuote(scriptId, planned);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const current = timeline?.script_id === scriptId ? timeline : null;

  async function generate() {
    setRunning(true);
    setMessage(null);
    try {
      const result = await api.generateEffects(scriptId, false);
      const made = result.outcomes.filter((o) => o.status === "generated").length;
      const reused = result.outcomes.filter((o) => o.status === "reused").length;
      setMessage({
        tone: "ok",
        text: `Generated ${made}${reused ? `, reused ${reused}` : ""} — ${usd(result.cost_micros)}.`,
      });
      onRefresh();
    } catch (e) {
      setMessage({ tone: "error", text: String(e).replace(/^Error:\s*/, "") });
    } finally {
      setRunning(false);
    }
  }

  const facts = [
    `${accepted.length} placement${accepted.length === 1 ? "" : "s"}`,
    planned.length ? `${planned.length} still to generate` : accepted.length ? "every one generated" : null,
    suggestions.length
      ? `${suggestions.length} suggestion${suggestions.length === 1 ? "" : "s"} waiting for you`
      : null,
  ].filter(Boolean);

  return (
    <>
      <PageHeader
        kicker={`${script?.title ?? "This episode"} · sound design`}
        title="Effects"
        subtitle={facts.join(" · ")}
        actions={
          planned.length > 0 && (
            <button
              type="button"
              className="primary large"
              disabled={running || quote === null}
              onClick={() => void generate()}
            >
              {running
                ? "Generating…"
                : failed
                  ? "Couldn't price it"
                  : quote === null
                    ? "Pricing…"
                    : quote.count === 0
                      ? `✨ Place ${planned.length} from the library — free`
                      : `✨ Generate ${quote.count} for ${usd(quote.cost)}`}
            </button>
          )
        }
      />

      {failed && (
        <p className="note error page-note" role="alert">
          Couldn't price the effects, so nothing can be spent on them yet: {failed}
        </p>
      )}
      {message && (
        <p className={`note ${message.tone} page-note`} role={message.tone === "error" ? "alert" : "status"}>
          {message.text}
        </p>
      )}

      {current && slots.length > 0 && (
        <section className="panel" aria-labelledby="where-h">
          <div className="card-head">
            <h2 id="where-h" className="card-title">
              🗺️ Where they sit
            </h2>
            <span className="card-sub flush">
              Effects play over the narration — they never add time. Click a sound to hear it in
              place.
            </span>
          </div>
          <WhereTheySit timeline={current} />
          {(accepted.length > sounds || suggestions.length > 0) && (
            <div className="pills">
              {accepted.length > sounds && (
                <span className="pill effect">
                  ♻️ {accepted.length} placements, {sounds} distinct sound{sounds === 1 ? "" : "s"} —
                  repeats are generated once and reused free
                </span>
              )}
              {suggestions.length > 0 && (
                <span className="pill quiet">
                  🪄 {suggestions.length} suggestion{suggestions.length === 1 ? "" : "s"} waiting —
                  nothing is generated until you accept it
                </span>
              )}
            </div>
          )}
        </section>
      )}

      <div className="row">
        <div className="grow">
          <h2 className="section-title">In this episode</h2>
          {slots.length === 0 ? (
            <div className="panel">
              <div className="empty">
                <strong>No effects yet</strong>
                Add <code>[SFX: description, 4s]</code> markers to the script, or add one here.
              </div>
            </div>
          ) : (
            <div className="fx-cards">
              {slots.map((slot, index) => {
                const same = slot.effect_id
                  ? slots.filter((s) => s.effect_id === slot.effect_id)
                  : [];
                return (
                  <FxCard
                    key={slot.id}
                    slot={slot}
                    reused={
                      slot.effect_id !== null &&
                      slots.slice(0, index).some((s) => s.effect_id === slot.effect_id)
                    }
                    others={Math.max(0, same.length - 1)}
                    timeline={current}
                    chunk={chunks.find((c) => c.ordinal === slot.at_chunk_ordinal)}
                    onChange={onRefresh}
                  />
                );
              })}
            </div>
          )}
        </div>

        <div className="side">
          <AddEffect scriptId={scriptId} chunks={chunks} onChange={onRefresh} />
          <p className="note side-note">
            A sound is generated once and placed as often as you like — repeats cost nothing. The
            export mixes the effects into an <code>_fx</code> version of each master, and keeps the
            plain master and each effect's own file for an editor who wants them on a separate
            track.
          </p>
        </div>
      </div>
    </>
  );
}

/** What generating the planned effects would cost, from a dry run — which
 *  spends nothing — repeated whenever the planned set changes. Without a price
 *  there is no button to spend with. */
function useQuote(scriptId: number, planned: Slot[]) {
  const [quote, setQuote] = useState<{ count: number; cost: number } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const key = planned.map((s) => s.id).join(",");

  useEffect(() => {
    setQuote(null);
    setFailed(null);
    if (!key) return;
    let alive = true;
    api
      .generateEffects(scriptId, true)
      .then((result) => {
        if (!alive) return;
        setQuote({
          count: result.outcomes.filter((o) => o.status === "would_generate").length,
          cost: result.cost_micros,
        });
      })
      .catch((e) => alive && setFailed(String(e).replace(/^Error:\s*/, "")));
    return () => {
      alive = false;
    };
  }, [scriptId, key]);

  return { quote, failed };
}
