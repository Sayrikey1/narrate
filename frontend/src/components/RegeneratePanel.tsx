import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { isName } from "../findings";
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
 *  by what the check finds — see the "put the new take in the cut" choices.
 *
 *  By default it keeps trying while a new take is still flagged, up to three
 *  times, and rebuilds the episode afterwards: the point is an episode with
 *  nothing left to hear, not one more take to compare. */

const TRIES: readonly [number, string][] = [
  [3, "Keep trying, up to 3 · recommended"],
  [2, "Try again, up to 2"],
  [1, "Stop — one try"],
];

const MOVES: readonly [CutMove, string][] = [
  ["better", "When it checks better · recommended"],
  ["new", "Unless it checks worse"],
  ["never", "Never — I'll choose after listening"],
];

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
  const panel = useRef<HTMLDivElement | null>(null);

  // Opening the panel moves focus into it, so keyboard users are not left on a
  // button that has just disappeared.
  useEffect(() => {
    panel.current?.querySelector<HTMLElement>("input, button")?.focus();
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

  // When every finding on the take in the cut is a name, the check is the
  // likelier culprit: say so before anybody pays to find out.
  const cutTake = single?.takes.find((t) => t.in_cut);
  const problems = cutTake?.findings.filter((f) => f.severity !== "info") ?? [];
  const allNames =
    !!single && problems.length > 0 && problems.every((f) => isName(f, single.text));

  return (
    <div
      className="regen"
      role="group"
      aria-label={`Regenerate chunk ${ordinals.join(", ")}`}
      ref={panel}
    >
      <h3 className="regen-title">
        🔁{" "}
        {single
          ? `Regenerate chunk ${single.ordinal}`
          : `Fix ${picked.length} flagged chunk${picked.length === 1 ? "" : "s"}`}
      </h3>

      {allNames && cutTake && (
        <div className="hint-box">
          <span aria-hidden>💡</span>
          <span>
            {problems.length === 1 ? "The finding" : "Every finding"} on take {cutTake.ordinal} is on a
            name — “{[...new Set(problems.map((f) => f.expected))].join("”, “")}” — and the checker often
            mishears names. Listen at {clock(problems[0]!.start_s)} first: if it sounds right, press{" "}
            <strong>It's fine</strong> on the take and pay nothing.
          </span>
        </div>
      )}

      <div className="regen-options">
        {single && (
          <label className="checkbox">
            <input
              type="checkbox"
              checked={rewording}
              disabled={busy}
              onChange={(e) => changed(setRewording)(e.target.checked)}
            />
            <span>
              ✏️ Change the words first
              <span className="checkbox-hint">
                Allowed only on models with no cross-chunk continuity (the v3 family) — elsewhere it
                would change what the neighbouring chunks are asked to say, and they would be
                generated and billed again.
              </span>
            </span>
          </label>
        )}
        {rewording && single && (
          <textarea
            className="regen-text"
            value={text}
            disabled={busy}
            rows={5}
            aria-label={`New words for chunk ${single.ordinal}`}
            onChange={(e) => changed(setText)(e.target.value)}
          />
        )}

        <fieldset className="seg-set">
          <legend>If the new take is still flagged</legend>
          <div className="seg">
            {TRIES.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={attempts === value}
                disabled={busy}
                onClick={() => changed(setAttempts)(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className="seg-set">
          <legend>Put the new take in the cut</legend>
          <div className="seg">
            {MOVES.map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={move === value}
                disabled={busy}
                onClick={() => changed(setMove)(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </fieldset>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={rebuild}
            disabled={busy}
            onChange={(e) => changed(setRebuild)(e.target.checked)}
          />
          <span>
            📦 Rebuild the episode afterwards
            <span className="checkbox-hint">In the formats chosen on this page.</span>
          </span>
        </label>
        {attempts > 1 && (
          <p className="hint">
            A try that comes back flagged is followed by another; once a plain try has failed, the
            next uses the steadiest delivery. It stops at the first clean take, so you often pay for
            less than the maximum.
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

      <div className="regen-actions">
        {quote && runnable.length > 0 ? (
          <p className="regen-quote">
            Up to <strong>{quote.worst_usd}</strong> at list price
            <span className="faint">
              {" "}
              ({quote.attempts > 1 ? `${quote.attempts} tries at most` : "one try"}) · the new take is
              checked with {quote.checked_with}, for free
            </span>
            {quote.mock && <span className="faint"> · Mock provider — nothing is billed.</span>}
          </p>
        ) : (
          <p className="regen-quote faint">Nothing is sent until you confirm the price.</p>
        )}
        <div className="push">
          <button type="button" className="large" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          {!quote ? (
            <button
              type="button"
              className="large"
              disabled={busy || pricing || (rewording && !text.trim())}
              onClick={price}
            >
              {pricing ? "Pricing…" : "Price it"}
            </button>
          ) : (
            <button
              type="button"
              className="primary large"
              disabled={busy || runnable.length === 0}
              onClick={run}
            >
              Regenerate for up to {quote.worst_usd}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
