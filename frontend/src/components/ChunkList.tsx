import { useState } from "react";
import type { ChunkRow, Finding, RegenerateBody, Take, Timeline } from "../types";
import { api, usd, peaksUrl } from "../api";
import { isName, spoken } from "../findings";
import { formatClock } from "../time";
import { Player } from "./Player";
import { usePeaks } from "./Wave";
import { RegeneratePanel } from "./RegeneratePanel";

/** How far before a flagged word playback starts, so the ear has context. */
const LEAD_IN_S = 1.5;

/** Chunks with their takes. Auditioning and promoting a take into the cut is
 *  the loop this screen exists for — and, when a take is not what was written,
 *  hearing exactly where and generating it again, or saying it is fine. */
export function ChunkList({
  chunks,
  scriptId,
  timeline,
  onChange,
  onRegenerate,
  onGenerateMissing,
  busy,
}: {
  chunks: ChunkRow[];
  scriptId: number;
  /** For each chunk's place in the episode, when known. */
  timeline?: Timeline | null;
  onChange: () => void;
  onRegenerate: (body: RegenerateBody) => Promise<void>;
  /** Open the generate panel for a chunk with no audio. */
  onGenerateMissing?: () => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState<number | null>(null);
  // Per take: where its player should jump to, and a nonce so the same spot
  // can be played twice.
  const [cues, setCues] = useState<Record<number, { at: number; nonce: number }>>({});
  // Chunks whose every take is shown, not only the latest few.
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  function toggleAll(ordinal: number) {
    setExpanded((prior) => {
      const next = new Set(prior);
      if (next.has(ordinal)) next.delete(ordinal);
      else next.add(ordinal);
      return next;
    });
  }

  if (!chunks.length) {
    return (
      <div className="empty">
        <strong>No chunks</strong>
        Add a script to this project first.
      </div>
    );
  }

  async function promote(chunkOrdinal: number, takeOrdinal: number) {
    await api.setCut(scriptId, chunkOrdinal, takeOrdinal);
    onChange();
  }

  async function accept(take: Take, accepted: boolean) {
    await api.acceptTake(take.id, accepted);
    onChange();
  }

  // Closing hands focus back to the button that opened the panel, rather than
  // dropping it on the page — the panel is gone, and so was the focused element.
  function close(ordinal: number) {
    setOpen(null);
    requestAnimationFrame(() => {
      document.querySelector<HTMLButtonElement>(`[data-regen="${ordinal}"]`)?.focus();
    });
  }

  function hear(take: Take, finding: Finding) {
    setCues((prior) => ({
      ...prior,
      [take.id]: {
        at: Math.max(0, finding.start_s - LEAD_IN_S),
        nonce: (prior[take.id]?.nonce ?? 0) + 1,
      },
    }));
  }

  const entries = new Map(
    (timeline?.entries ?? [])
      .filter((e) => e.kind === "narration" && e.chunk_ordinal !== null)
      .map((e) => [e.chunk_ordinal as number, e]),
  );

  return (
    <div className="chunk-list">
      {chunks.map((chunk) => {
        const entry = entries.get(chunk.ordinal);
        const succeeded = chunk.takes.filter((t) => t.status === "succeeded");
        const noAudio = succeeded.length === 0 && chunk.takes.length > 0;
        // The take in the cut first, with what the check found: it is the one
        // the episode plays. The others are there to listen to and swap in.
        const cut = succeeded.find((t) => t.in_cut);
        const others = succeeded.filter((t) => !t.in_cut).sort((a, b) => b.ordinal - a.ordinal);
        const showAll = expanded.has(chunk.ordinal);
        const shownOthers = showAll ? others : others.slice(0, MOST_OTHERS);
        const failed = chunk.takes
          .filter((t) => t.status !== "succeeded")
          .sort((a, b) => b.ordinal - a.ordinal);
        const isOpen = open === chunk.ordinal;

        return (
          <article
            key={chunk.ordinal}
            aria-label={`Chunk ${chunk.ordinal}`}
            className={["chunk", isOpen ? "open" : "", noAudio ? "failed" : ""].filter(Boolean).join(" ")}
          >
            <div className="chunk-head">
              <span className="chunk-num">{chunk.ordinal}</span>
              <div className="chunk-text">
                <span>
                  “{opening(chunk.text)}”
                </span>
                <span className="chunk-meta">
                  <span className="mono">{chunk.chars.toLocaleString()}</span> chars
                  {entry && entry.duration_s > 0 && (
                    <>
                      {" · "}
                      {formatClock(entry.start_s)} – {formatClock(entry.end_s)}
                    </>
                  )}
                  {entry?.drift_s ? (
                    <span className={Math.abs(entry.drift_s) > 10 ? "warn" : undefined}>
                      {" · "}🎯 aimed at {formatClock(entry.target_s ?? 0)}, landed{" "}
                      {entry.drift_s > 0 ? "+" : ""}
                      {entry.drift_s.toFixed(1)}s
                    </span>
                  ) : null}
                  {chunk.source === "hard" && (
                    <span className="warn"> · split mid-sentence — consider adjusting</span>
                  )}
                </span>
              </div>
              {chunk.takes.length > 0 &&
                (isOpen ? (
                  <button type="button" className="small" onClick={() => close(chunk.ordinal)} aria-expanded>
                    Cancel
                  </button>
                ) : (
                  <button
                    type="button"
                    className="ghost small regen-toggle"
                    // aria-disabled rather than disabled: a disabled button cannot
                    // take focus, and focus returns here when the panel closes —
                    // including just after a regeneration has started.
                    aria-disabled={busy}
                    aria-expanded={false}
                    data-regen={chunk.ordinal}
                    onClick={() => {
                      if (!busy) setOpen(chunk.ordinal);
                    }}
                    title="Generate this chunk again — the price is shown first"
                  >
                    Regenerate…
                  </button>
                ))}
            </div>

            {chunk.takes.length === 0 && (
              <div className="take other">
                <span className="faint">Not generated yet.</span>
                {onGenerateMissing && (
                  <button type="button" className="primary small" onClick={onGenerateMissing}>
                    Generate it…
                  </button>
                )}
              </div>
            )}

            {cut && (
              <div className="take-block">
                <TakeRow
                  take={cut}
                  busy={busy}
                  cue={cues[cut.id] ?? null}
                  onUse={() => promote(chunk.ordinal, cut.ordinal)}
                  onAccept={(accepted) => void accept(cut, accepted)}
                />
                <Findings take={cut} text={chunk.text} onHear={(f) => hear(cut, f)} />
              </div>
            )}
            {shownOthers.map((take) => (
              <TakeRow
                key={take.id}
                take={take}
                busy={busy}
                cue={cues[take.id] ?? null}
                onUse={() => promote(chunk.ordinal, take.ordinal)}
              />
            ))}
            {others.length > MOST_OTHERS && (
              <button
                type="button"
                className="ghost small more-takes"
                aria-expanded={showAll}
                onClick={() => toggleAll(chunk.ordinal)}
              >
                {showAll
                  ? "Show fewer takes"
                  : `Show ${others.length - MOST_OTHERS} more take${others.length - MOST_OTHERS === 1 ? "" : "s"}`}
              </button>
            )}

            {noAudio
              ? failed.map((take, index) => (
                  <div key={take.id} className="take failed">
                    <span className="tag">take {take.ordinal}</span>
                    <span className="tag bad">⛔ {take.error?.headline ?? "didn't generate"}</span>
                    <span className="take-why">
                      {take.error ? (
                        <>
                          {take.error.detail} {take.error.fix}
                        </>
                      ) : (
                        <>
                          This attempt failed, so there is nothing to play —{" "}
                          <span className="mono">{usd(take.cost_micros)}</span>.
                        </>
                      )}
                    </span>
                    {index === 0 && onGenerateMissing && (
                      <button type="button" className="primary small" onClick={onGenerateMissing}>
                        Generate it…
                      </button>
                    )}
                  </div>
                ))
              : failed.length > 0 && (
                  <p className="faint take-history">
                    {failed.length} earlier attempt{failed.length === 1 ? "" : "s"} failed
                    {failed[0]?.error ? ` — the last because ${lowerFirst(failed[0].error.headline)}` : ""} ·{" "}
                    <span className="mono">{usd(failed.reduce((sum, t) => sum + t.cost_micros, 0))}</span>
                  </p>
                )}

            {isOpen && (
              <RegeneratePanel
                scriptId={scriptId}
                chunks={[chunk]}
                busy={busy}
                onRun={onRegenerate}
                onClose={() => close(chunk.ordinal)}
              />
            )}
          </article>
        );
      })}
    </div>
  );
}

/** Takes besides the one in the cut shown before "Show more". */
const MOST_OTHERS = 2;

/** The first words of a chunk as they are heard. */
function opening(text: string): string {
  const words = spoken(text);
  return words.length > 140 ? `${words.slice(0, 140).trim()}…` : words;
}

/** One take: whether it is in the cut, how it checked, a player, and what can
 *  be done with it — swap it in, or, in the cut and flagged, say it is fine. */
function TakeRow({
  take,
  busy,
  cue,
  onUse,
  onAccept,
}: {
  take: Take;
  busy: boolean;
  cue: { at: number; nonce: number } | null;
  onUse: () => void;
  /** Only for the take in the cut: the one whose flag matters. */
  onAccept?: (accepted: boolean) => void;
}) {
  const peaks = usePeaks(peaksUrl.take(take.id, 64));
  return (
    <div className={take.in_cut ? "take" : "take other"}>
      {take.in_cut ? (
        <span className="tag cut">✓ take {take.ordinal} · in the cut</span>
      ) : (
        <span className="tag">take {take.ordinal}</span>
      )}
      <CheckBadge take={take} />
      <Player src={api.audioTake(take.id)} duration={take.duration_s} cue={cue} peaks={peaks} />
      <span className="take-cost">{usd(take.cost_micros)}</span>
      {!take.in_cut && (
        <button type="button" className="small" disabled={busy} onClick={onUse}>
          use this
        </button>
      )}
      {onAccept &&
        flaggedByCheck(take) &&
        (take.verify_accepted ? (
          <button
            type="button"
            className="ghost small"
            onClick={() => onAccept(false)}
            aria-label={`Flag take ${take.ordinal} again`}
            title="Report this take as flagged again"
          >
            undo
          </button>
        ) : (
          <button
            type="button"
            className="ghost small"
            onClick={() => onAccept(true)}
            title="You listened and it is right — a name the check cannot spell, say. Stops it being flagged; what was found stays below."
          >
            It's fine
          </button>
        ))}
    </div>
  );
}

function flaggedByCheck(take: Take): boolean {
  return take.verify_status === "suspect" || take.verify_status === "review";
}

/** A take's verification, in words. There is no tick: "no issues found"
 *  means the checks found nothing, and a word clipped short but still
 *  recognisable passes every one of them. */
function CheckBadge({ take }: { take: Take }) {
  const fails = take.findings.filter((f) => f.severity === "fail").length;
  if (flaggedByCheck(take) && take.verify_accepted) {
    return (
      <span className="tag quiet" title="You listened and said it is fine — what the check found is below">
        fine by ear
      </span>
    );
  }
  switch (take.verify_status) {
    case "suspect":
      return (
        <span className="tag bad" title="Words missing, added or garbled — listen below">
          🚩 flagged · {fails}
        </span>
      );
    case "review":
      return (
        <span className="tag warn" title="Probably fine, worth a listen">
          👀 review
        </span>
      );
    case "clear":
      return (
        <span className="tag quiet" title="The checks found nothing — not a guarantee">
          no issues found
        </span>
      );
    case "error":
      return <span className="tag bad">check failed</span>;
    default:
      return null;
  }
}

/** Enough to act on. A take that is mostly wrong — truncated, or silent —
 *  can produce dozens of findings, and the first few say what needs saying. */
const MOST_FINDINGS = 4;

function Findings({
  take,
  text,
  onHear,
}: {
  take: Take;
  /** The chunk's words, to tell a name from an ordinary word. */
  text: string;
  onHear: (f: Finding) => void;
}) {
  const problems = take.findings.filter((f) => f.severity !== "info");
  if (!problems.length) return null;
  const shown = problems.slice(0, MOST_FINDINGS);
  const hidden = problems.length - shown.length;
  return (
    // Kept when accepted, so the spots can be heard again before an undo.
    <ul className={take.verify_accepted ? "findings accepted" : "findings"}>
      {shown.map((finding, index) => (
        <li key={index} className={finding.severity}>
          <button
            type="button"
            className="finding-time"
            onClick={() => onHear(finding)}
            title="Play this take from just before the spot"
            aria-label={`Play take ${take.ordinal} at ${clock(finding.start_s)}`}
          >
            ▶ {clock(finding.start_s)}
          </button>
          <span>
            {finding.summary}
            {finding.note ? (
              <span className="faint"> — {finding.note}</span>
            ) : isName(finding, text) ? (
              <span className="faint"> — names are often misheard, so listen before re-rolling</span>
            ) : null}
          </span>
        </li>
      ))}
      {hidden > 0 && <li className="faint">and {hidden} more on this take.</li>}
    </ul>
  );
}

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** "ElevenLabs ran out of credits" to follow "because". */
function lowerFirst(text: string): string {
  return /^[A-Z][a-z]/.test(text) && !text.startsWith("ElevenLabs") ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}
