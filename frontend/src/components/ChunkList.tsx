import { Fragment, useState } from "react";
import type { ChunkRow, Finding, RegenerateBody, Take } from "../types";
import { api, usd } from "../api";
import { CheckIcon } from "./Icon";
import { Player } from "./Player";
import { RegeneratePanel } from "./RegeneratePanel";

/** How far before a flagged word playback starts, so the ear has context. */
const LEAD_IN_S = 1.5;

/** Chunks with their takes. Auditioning and promoting a take into the cut is
 *  the loop this screen exists for — and, when a take is not what was written,
 *  hearing exactly where and generating it again. */
export function ChunkList({
  chunks,
  scriptId,
  onChange,
  onRegenerate,
  busy,
}: {
  chunks: ChunkRow[];
  scriptId: number;
  onChange: () => void;
  onRegenerate: (body: RegenerateBody) => Promise<void>;
  busy: boolean;
}) {
  const [open, setOpen] = useState<number | null>(null);
  // Per take: where its player should jump to, and a nonce so the same spot
  // can be played twice.
  const [cues, setCues] = useState<Record<number, { at: number; nonce: number }>>({});

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

  return (
    <table className="chunks">
      <thead>
        <tr>
          <th className="num">#</th>
          <th className="num">Chars</th>
          <th>Opening</th>
          <th>Takes</th>
        </tr>
      </thead>
      <tbody>
        {chunks.map((chunk) => (
          <Fragment key={chunk.ordinal}>
            <tr className={open === chunk.ordinal ? "regen-open" : undefined}>
              <td className="num dim">{chunk.ordinal}</td>
              <td className="num mono">{chunk.chars.toLocaleString()}</td>
              <td>
                {chunk.text.slice(0, 90)}
                {chunk.text.length > 90 ? "…" : ""}
                {chunk.source === "hard" && (
                  <div className="note warn">split mid-sentence — consider adjusting</div>
                )}
              </td>
              <td>
                {chunk.takes.length === 0 && <span className="faint">not generated</span>}
                {chunk.takes.map((take) => (
                  <div key={take.id} className="take">
                    <div className="take-row">
                      <span className={take.in_cut ? "tag cut" : "tag"}>
                        {take.in_cut && <CheckIcon />}
                        take {take.ordinal}
                      </span>
                      <CheckBadge take={take} />
                      <span className="faint mono">
                        {take.duration_s ? `${take.duration_s.toFixed(2)}s · ` : ""}
                        {usd(take.cost_micros)}
                      </span>
                      <Player
                        src={api.audioTake(take.id)}
                        duration={take.duration_s}
                        cue={cues[take.id] ?? null}
                      />
                      {!take.in_cut && take.status === "succeeded" && (
                        <button
                          className="small"
                          disabled={busy}
                          onClick={() => promote(chunk.ordinal, take.ordinal)}
                        >
                          use this
                        </button>
                      )}
                      {flaggedByCheck(take) &&
                        (take.verify_accepted ? (
                          <button
                            className="ghost small"
                            onClick={() => void accept(take, false)}
                            aria-label={`Flag take ${take.ordinal} again`}
                            title="Report this take as flagged again"
                          >
                            undo
                          </button>
                        ) : (
                          <button
                            className="ghost small"
                            onClick={() => void accept(take, true)}
                            title="You listened and it is right — a name the check cannot spell, say. Stops it being flagged; what was found stays below."
                          >
                            It's fine
                          </button>
                        ))}
                    </div>
                    <Findings take={take} onHear={(f) => hear(take, f)} />
                  </div>
                ))}
                {chunk.takes.length > 0 && open !== chunk.ordinal && (
                  <button
                    className="ghost small regen-toggle"
                    // aria-disabled rather than disabled: a disabled button cannot
                    // take focus, and focus returns here when the panel closes —
                    // including just after a regeneration has started.
                    aria-disabled={busy}
                    data-regen={chunk.ordinal}
                    onClick={() => {
                      if (!busy) setOpen(chunk.ordinal);
                    }}
                    title="Generate this chunk again — the price is shown first"
                  >
                    Regenerate…
                  </button>
                )}
              </td>
            </tr>
            {open === chunk.ordinal && (
              <tr className="regen-row">
                <td colSpan={4}>
                  <RegeneratePanel
                    scriptId={scriptId}
                    chunks={[chunk]}
                    busy={busy}
                    onRun={onRegenerate}
                    onClose={() => close(chunk.ordinal)}
                  />
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
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
          flagged · {fails}
        </span>
      );
    case "review":
      return (
        <span className="tag warn" title="Probably fine, worth a listen">
          review
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

function flaggedByCheck(take: Take): boolean {
  return take.verify_status === "suspect" || take.verify_status === "review";
}

/** Enough to act on. A take that is mostly wrong — truncated, or silent —
 *  can produce dozens of findings, and the first few say what needs saying. */
const MOST_FINDINGS = 4;

function Findings({ take, onHear }: { take: Take; onHear: (f: Finding) => void }) {
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
            className="finding-time"
            onClick={() => onHear(finding)}
            title="Play this take from just before the spot"
            aria-label={`Play take ${take.ordinal} at ${clock(finding.start_s)}`}
          >
            ▶ {clock(finding.start_s)}
          </button>
          <span>
            {finding.summary}
            {finding.note && <span className="faint"> — {finding.note}</span>}
          </span>
        </li>
      ))}
      {hidden > 0 && (
        <li className="faint">
          and {hidden} more on this take.
        </li>
      )}
    </ul>
  );
}

function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
