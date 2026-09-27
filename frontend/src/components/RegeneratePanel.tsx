import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChunkRow, CutMove, RegenerateBody, RegenerateQuote } from "../types";

/** Generating chunks again — price first, then confirm.
 *
 *  Two steps on purpose. "Price it" sends nothing: it asks the server what the
 *  regeneration would cost and whether anything prevents it (a request whose
 *  outcome is unknown, or a model where rewording would re-bill the
 *  neighbouring chunks). Only the second button spends, and it names the
 *  amount. Changing any option discards the quote, so the figure on the button
 *  is always the figure for exactly what will be sent.
 *
 *  Every earlier take is kept. Whether the cut moves to the new one is decided
 *  by what the check finds — see the "use the new take" choices below.
 *
 *  By default it keeps trying while a new take is still flagged, up to three
 *  times, and rebuilds the episode afterwards: the point is an episode with
 *  nothing left to hear, not one more take to compare. */
export function RegeneratePanel({
  scriptId,
  chunks,
  busy,
  onRun,
  onClose,
}: {
  scriptId: number;
  chunks: ChunkRow[];
  busy: boolean;
  onRun: (body: RegenerateBody) => Promise<void>;
  onClose: () => void;
}) {
  // The chunks it was opened for, fixed at that moment: a refresh that flags
  // another chunk must not slip it into a run priced without it.
  const [picked] = useState(chunks);
  // Rewording is one chunk's words, so it is offered only for one chunk.
  const single = picked.length === 1 ? picked[0] : null;
  const ordinals = picked.map((c) => c.ordinal);
  const [rewording, setRewording] = useState(false);
  const [text, setText] = useState(single?.text ?? "");
  const [attempts, setAttempts] = useState(3);
  const [move, setMove] = useState<CutMove>("better");
  const [acceptUnknown, setAcceptUnknown] = useState(false);
  const [rebuild, setRebuild] = useState(true);
  const [quote, setQuote] = useState<RegenerateQuote | null>(null);
  const [pricing, setPricing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped on every change, so a price that arrives after the options moved on
  // is recognised as stale and dropped — the button must only ever show the
  // price of exactly what it will send.
  const version = useRef(0);
  const first = useRef<HTMLInputElement | HTMLSelectElement | null>(null);

  // Opening the panel moves focus into it, so keyboard users are not left on a
  // button that has just disappeared.
  useEffect(() => {
    first.current?.focus();
  }, []);

  const body = (): RegenerateBody => ({
    chunks: ordinals,
    attempts,
    move,
    export: rebuild,
    ...(rewording && single ? { text } : {}),
    ...(acceptUnknown ? { accept_unknown: true } : {}),
  });

  // Any change makes the old price wrong, so it is thrown away.
  function changed<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value);
      version.current += 1;
      setQuote(null);
      setError(null);
    };
  }

  async function price() {
    const asked = version.current;
    setPricing(true);
    setError(null);
    try {
      const answer = await api.regenerate(scriptId, { ...body(), confirm: false });
      if (asked === version.current) setQuote(answer);
    } catch (e) {
      if (asked === version.current) setError(String(e));
    } finally {
      setPricing(false);
    }
  }

  async function run() {
    setError(null);
    try {
      if (!quote) return;
      // The figure on the button is sent as the limit, so the server can never
      // spend more than was shown — whatever changed since it was priced.
      await onRun({ ...body(), confirm: true, max_spend_usd: quote.worst_micros / 1_000_000 });
      onClose();
    } catch (e) {
      setError(String(e));
    }
  }

  // Blocked chunks are skipped, not a reason to fix none: the others still run.
  const blocked = quote?.plans.filter((p) => p.blocker) ?? [];
  const runnable = quote?.plans.filter((p) => acceptUnknown || !p.blocker) ?? [];

  return (
    <div className="regen" role="group" aria-label={`Regenerate chunk ${ordinals.join(", ")}`}>
      <div className="regen-options">
        {single && (
          <label className="checkbox">
            <input
              ref={(el) => {
                first.current = el;
              }}
              type="checkbox"
              checked={rewording}
              disabled={busy}
              onChange={(e) => changed(setRewording)(e.target.checked)}
            />
            Change the words first
          </label>
        )}
        {rewording && single && (
          <textarea
            className="regen-text"
            value={text}
            disabled={busy}
            rows={6}
            aria-label={`New words for chunk ${single.ordinal}`}
            onChange={(e) => changed(setText)(e.target.value)}
          />
        )}
        {rewording && (
          <p className="hint">
            Rewording is only allowed on models with no cross-chunk continuity
            (the v3 family) — elsewhere it would change what the neighbouring
            chunks are asked to say, and they would be generated and billed again.
          </p>
        )}

        <label>
          If the new take is still flagged
          <select
            ref={(el) => {
              if (!single) first.current = el;
            }}
            value={attempts}
            disabled={busy}
            onChange={(e) => changed(setAttempts)(Number(e.target.value))}
          >
            <option value={3}>keep trying, up to 3 tries (recommended)</option>
            <option value={2}>try again, up to 2 tries</option>
            <option value={1}>stop — one try</option>
          </select>
        </label>

        <label>
          Use the new take
          <select
            value={move}
            disabled={busy}
            onChange={(e) => changed(setMove)(e.target.value as CutMove)}
          >
            <option value="better">when it checks better (recommended)</option>
            <option value="new">unless it checks worse</option>
            <option value="never">never — I'll choose after listening</option>
          </select>
        </label>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={rebuild}
            disabled={busy}
            onChange={(e) => changed(setRebuild)(e.target.checked)}
          />
          Rebuild the episode afterwards
        </label>
        {attempts > 1 && (
          <p className="hint">
            A try that comes back flagged is followed by another; once a plain try has
            failed, the next uses the steadiest delivery, which invents fewer words but
            follows audio tags more softly. It stops at the first clean take.
          </p>
        )}
      </div>

      {error && <div className="note error">{error}</div>}
      {!acceptUnknown &&
        blocked.map((p) => (
          <div key={p.ordinal} className="note warn">
            {runnable.length ? `Chunk ${p.ordinal} will be skipped. ` : ""}
            {p.blocker}
          </div>
        ))}
      {blocked.length > 0 && (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={acceptUnknown}
            disabled={busy}
            onChange={(e) => changed(setAcceptUnknown)(e.target.checked)}
          />
          I have checked — regenerate anyway, accepting it may bill twice
        </label>
      )}

      {quote && runnable.length > 0 && (
        <p className="regen-quote">
          Up to <strong>{quote.worst_usd}</strong> at list price
          {quote.attempts > 1 ? ` (${quote.attempts} tries at most)` : ""}. The new
          take is checked with {quote.checked_with}.
          {quote.mock && <span className="dim"> Mock provider — nothing is billed.</span>}
        </p>
      )}

      <div className="regen-actions">
        {!quote ? (
          <button disabled={busy || pricing || (rewording && !text.trim())} onClick={price}>
            {pricing ? "Pricing…" : "Price it"}
          </button>
        ) : (
          <button className="primary" disabled={busy || runnable.length === 0} onClick={run}>
            Regenerate for up to {quote.worst_usd}
          </button>
        )}
        <button className="ghost" disabled={busy} onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
