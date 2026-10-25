import { useEffect, useRef, useState } from "react";
import { api, usd } from "../api";
import { Link, scriptPath } from "../router";
import { Notice, Skeleton } from "../ui/feedback";
import { CloseIcon, SparkleIcon } from "./Icon";
import type { Cost, GenerateStarted, Slot } from "../types";

type Projection = GenerateStarted["projection"];

/** Generate what is outstanding — with the price first.
 *
 *  Opening the panel asks the server for a dry run, which sends nothing, and
 *  the table shows what it says. Only the button spends, it names the amount,
 *  and that amount goes with the request as a hard limit. Changing whether the
 *  planned effects are included throws the price away and asks again, so the
 *  figure on the button is always for exactly what will be sent.
 *
 *  A dry run is still a run on the server, if a brief one; the button waits
 *  for it to end, or pressing it straight away would be refused as "already
 *  running". */
export function GeneratePanel({
  scriptId,
  slots,
  missing,
  cost,
  busy,
  running,
  log,
  onGenerate,
  onClose,
}: {
  scriptId: number;
  slots: Slot[];
  /** Chunk numbers without audio, to name them rather than count them. */
  missing: number[];
  cost: Cost | null;
  busy: boolean;
  /** A generation started from here is under way. */
  running: boolean;
  log: string[];
  onGenerate: (withEffects: boolean, maxUsd: number) => void;
  onClose: () => void;
}) {
  const [withEffects, setWithEffects] = useState(true);
  const [quote, setQuote] = useState<Projection | null>(null);
  const [settled, setSettled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const version = useRef(0);

  const planned = slots.filter((s) => s.accepted && !s.effect_id);

  useEffect(() => {
    if (running) return;
    const asked = ++version.current;
    setQuote(null);
    setSettled(false);
    setError(null);
    let stop: (() => void) | undefined;
    api
      .generate(scriptId, { dry_run: true, with_effects: withEffects })
      .then((started) => {
        if (asked !== version.current) return;
        setQuote(started.projection);
        stop = api.streamRun(
          started.run_key,
          () => undefined,
          () => {
            if (asked === version.current) setSettled(true);
          },
        );
      })
      .catch((e) => {
        if (asked === version.current) setError(String(e).replace(/^Error:\s*/, ""));
      });
    return () => stop?.();
  }, [scriptId, withEffects, running]);

  const total = quote?.total_micros ?? 0;
  const nothing = !!quote && quote.chunks === 0 && quote.effects === 0;
  // Four places for small sums: "$0.00" for a two-cent run would be a lie.
  const price = usd(total, total < 100_000 ? 4 : 2);

  return (
    <section className="panel generate" id="generate" aria-labelledby="gen-h">
      <div className="card-head">
        <div className="card-head-text">
          <h2 className="card-title" id="gen-h">
            ✦ Generate what's outstanding
          </h2>
          <p className="card-sub">
            Here's the price first. Nothing is sent until you press the button, and the amount on
            it is the most the run can spend.
          </p>
        </div>
        <button type="button" className="round" aria-label="Close the generate panel" onClick={onClose}>
          <CloseIcon />
        </button>
      </div>

      {error && <Notice tone="error">{error}</Notice>}

      {running || log.length > 0 ? (
        <div className="log" role="status" aria-live="polite">
          {log.join("\n")}
        </div>
      ) : !quote ? (
        !error && <Skeleton rows={3} />
      ) : nothing ? (
        <Notice tone="ok" title="Nothing to generate">
          Every chunk has a take and every accepted effect has audio.
        </Notice>
      ) : (
        <div className="price">
          <table>
            <caption className="sr-only">What this run will cost</caption>
            <tbody>
              <tr>
                <th scope="row">🎙️ Narration</th>
                <td className="dim">
                  {quote.chunks === missing.length && missing.length > 0
                    ? `Chunk ${missing.join(", ")}`
                    : `${quote.chunks} chunk${quote.chunks === 1 ? "" : "s"}`}{" "}
                  · <span className="mono">{quote.chars.toLocaleString()}</span> chars
                </td>
                <td className="num mono">{usd(quote.speech_micros)}</td>
              </tr>
              {planned.length > 0 && (
                <tr className={withEffects ? undefined : "excluded"}>
                  <th scope="row">✨ Effects</th>
                  <td className="dim">
                    {planned.map((s) => s.description).join(" · ")}
                    {withEffects && quote.effects > 0 && (
                      <>
                        {" "}
                        — <span className="mono">{quote.effect_seconds} s</span>
                      </>
                    )}
                  </td>
                  <td className="num mono">{withEffects ? usd(quote.effect_micros) : "not included"}</td>
                </tr>
              )}
              <tr className="total">
                <th scope="row">Total</th>
                <td className="dim">
                  {[
                    quote.chunks > 0 && `${quote.chunks} chunk${quote.chunks === 1 ? "" : "s"}`,
                    quote.effects > 0 && `${quote.effects} effect${quote.effects === 1 ? "" : "s"}`,
                  ]
                    .filter(Boolean)
                    .join(" + ")}
                </td>
                <td className="num mono">{usd(total)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {!running && log.length === 0 && quote && !nothing && (
        <>
          <div className="generate-options">
            {planned.length > 0 && (
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={withEffects}
                  disabled={busy}
                  onChange={(e) => setWithEffects(e.target.checked)}
                />
                Include the {planned.length} planned effect{planned.length === 1 ? "" : "s"}
              </label>
            )}
            {cost?.cap_micros != null && <CapAfter cost={cost} adds={total} />}
          </div>

          <p className="note flush">
            One Generate covers the narration and the accepted effects; the cap is checked against
            both together. <Link to={scriptPath(scriptId, "/effects")}>Manage effects →</Link>
          </p>

          <div className="generate-go">
            <button
              type="button"
              className="primary go"
              disabled={!settled || busy}
              onClick={() => onGenerate(withEffects, total / 1_000_000)}
            >
              <SparkleIcon />
              {settled ? `Generate for ${price}` : "Pricing…"}
            </button>
            <button type="button" className="large" onClick={onClose}>
              Cancel
            </button>
            <span className="faint">⏱️ The run carries on if you leave this page.</span>
          </div>
        </>
      )}
    </section>
  );
}

/** The month's cap with this run on top of it. */
function CapAfter({ cost, adds }: { cost: Cost; adds: number }) {
  const cap = cost.cap_micros ?? 0;
  const used = (cap * (cost.cap_used_pct ?? 0)) / 100;
  const after = cap ? Math.round(((used + adds) / cap) * 100) : 0;
  const tone = after >= 100 ? " danger" : after >= 80 ? " warn" : "";
  return (
    <div className="cap">
      <div className="meter-head">
        <span>Monthly cap after this run</span>
        <span>
          {cost.cap_used_pct}% → {after}% of {usd(cap, 2)}
        </span>
      </div>
      <div className={`meter${tone}`} aria-hidden>
        <div style={{ width: `${Math.min(100, cost.cap_used_pct ?? 0)}%` }} />
        <div className="meter-add" style={{ width: `${Math.max(0, Math.min(100, after) - (cost.cap_used_pct ?? 0))}%` }} />
      </div>
    </div>
  );
}
