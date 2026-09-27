import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChunkRow, CutMove, RegenerateBody, RegenerateQuote } from "../types";

/** Generating one chunk again — price first, then confirm.
 *
 *  Two steps on purpose. "Price it" sends nothing: it asks the server what the
 *  regeneration would cost and whether anything prevents it (a request whose
 *  outcome is unknown, or a model where rewording would re-bill the
 *  neighbouring chunks). Only the second button spends, and it names the
 *  amount. Changing any option discards the quote, so the figure on the button
 *  is always the figure for exactly what will be sent.
 *
 *  Every earlier take is kept. Whether the cut moves to the new one is decided
 *  by what the check finds — see the "use the new take" choices below. */
export function RegeneratePanel({
  scriptId,
  chunk,
  busy,
  onRun,
  onClose,
}: {
  scriptId: number;
  chunk: ChunkRow;
  busy: boolean;
  onRun: (body: RegenerateBody) => Promise<void>;
  onClose: () => void;
}) {
  const [rewording, setRewording] = useState(false);
  const [text, setText] = useState(chunk.text);
  const [attempts, setAttempts] = useState(1);
  const [move, setMove] = useState<CutMove>("better");
  const [acceptUnknown, setAcceptUnknown] = useState(false);
  const [quote, setQuote] = useState<RegenerateQuote | null>(null);
  const [pricing, setPricing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped on every change, so a price that arrives after the options moved on
  // is recognised as stale and dropped — the button must only ever show the
  // price of exactly what it will send.
  const version = useRef(0);
  const first = useRef<HTMLInputElement | null>(null);

  // Opening the panel moves focus into it, so keyboard users are not left on a
  // button that has just disappeared.
  useEffect(() => {
    first.current?.focus();
  }, []);

  const body = (): RegenerateBody => ({
    chunks: [chunk.ordinal],
    attempts,
    move,
    ...(rewording ? { text } : {}),
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
      await onRun({ ...body(), confirm: true });
      onClose();
    } catch (e) {
      setError(String(e));
    }
  }

  const blocker = acceptUnknown ? null : quote?.plans.find((p) => p.blocker)?.blocker;
  const hadBlocker = Boolean(quote?.plans.some((p) => p.blocker));

  return (
    <div className="regen" role="group" aria-label={`Regenerate chunk ${chunk.ordinal}`}>
      <div className="regen-options">
        <label className="checkbox">
          <input
            ref={first}
            type="checkbox"
            checked={rewording}
            disabled={busy}
            onChange={(e) => changed(setRewording)(e.target.checked)}
          />
          Change the words first
        </label>
        {rewording && (
          <textarea
            className="regen-text"
            value={text}
            disabled={busy}
            rows={6}
            aria-label={`New words for chunk ${chunk.ordinal}`}
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
            value={attempts}
            disabled={busy}
            onChange={(e) => changed(setAttempts)(Number(e.target.value))}
          >
            <option value={1}>stop — one try</option>
            <option value={2}>try again, up to 2 tries</option>
            <option value={3}>try again, up to 3 tries</option>
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
      </div>

      {error && <div className="note error">{error}</div>}
      {blocker && <div className="note warn">{blocker}</div>}
      {(blocker || (hadBlocker && acceptUnknown)) && (
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

      {quote && !blocker && (
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
          <button className="primary" disabled={busy || Boolean(blocker)} onClick={run}>
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
