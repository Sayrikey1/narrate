import type { ChunkRow, ExportResult, Publish, Timeline } from "../types";

/** Where the episode is: script, audio, check, export, publish.
 *
 *  Each step says what is in the way in words, and draws it as a bar — so
 *  "Export is blocked" is never a greyed-out button with a tooltip again. */
export function Stepper({
  chunks,
  timeline,
  exported,
  publish,
}: {
  chunks: ChunkRow[];
  timeline: Timeline | null;
  /** An export made while this page was open. */
  exported: ExportResult | null;
  publish: Publish | null;
}) {
  const total = chunks.length;
  const chars = chunks.reduce((sum, c) => sum + c.chars, 0);
  const missing = (timeline?.entries ?? []).filter((e) => e.kind === "narration" && !e.generated);
  const generated = total - missing.length;
  const failed = chunks.filter(
    (c) => !c.takes.some((t) => t.status === "succeeded") && c.takes.some((t) => t.status === "failed"),
  );

  const inCut = chunks.flatMap((c) => c.takes.filter((t) => t.in_cut));
  const count = (test: (s: string, accepted: boolean) => boolean) =>
    inCut.filter((t) => test(t.verify_status, t.verify_accepted)).length;
  const clear = count((s) => s === "clear");
  const byEar = count((s, a) => a && (s === "suspect" || s === "review"));
  const review = count((s, a) => s === "review" && !a);
  const flagged = count((s, a) => s === "suspect" && !a);
  const unchecked = count((s) => s === "unverified" || s === "error");
  const checked = clear + byEar + review + flagged;

  const complete = total > 0 && missing.length === 0;
  const chapters = publish?.data.chapters;

  return (
    <ol className="stepper" aria-label="Where this episode is">
      <Step icon="📝" name="Script" state="✓" done bar="var(--ok)">
        {total} chunk{total === 1 ? "" : "s"} · {chars.toLocaleString()} chars
      </Step>

      <Step
        icon="🎙️"
        name="Audio"
        state={complete ? "✓" : `${generated}/${total}`}
        done={complete}
        tone={complete ? undefined : "warn"}
        bar={split([
          [generated, "var(--ok)"],
          [missing.length, "var(--badf)"],
        ])}
      >
        {complete
          ? "Every chunk has audio"
          : failed.length
            ? `Chunk ${failed.map((c) => c.ordinal).join(", ")} didn't generate`
            : `${missing.length} chunk${missing.length === 1 ? "" : "s"} not generated yet`}
      </Step>

      <Step
        icon="🔍"
        name="Check"
        state={
          flagged
            ? `${flagged} flagged`
            : review
              ? `${review} to review`
              : unchecked || !checked
                ? "not checked"
                : "✓"
        }
        done={!flagged && !review && !unchecked && checked > 0}
        tone={flagged ? "bad" : review ? "warn" : "faint"}
        bar={split([
          [clear + byEar, "var(--ok)"],
          [review, "var(--warnf)"],
          [flagged, "var(--badf)"],
          [unchecked, "var(--off)"],
        ])}
      >
        {[
          clear && `${clear} clear`,
          review && `${review} to review`,
          flagged && `${flagged} flagged`,
          byEar && `${byEar} fine by ear`,
          unchecked && `${unchecked} not checked`,
        ]
          .filter(Boolean)
          .join(" · ") || "Nothing to check yet"}
      </Step>

      <Step
        icon="📦"
        name="Export"
        state={exported ? "✓" : complete ? "ready" : "blocked"}
        done={!!exported}
        tone={complete ? "ok" : "warn"}
        bar={
          exported || complete
            ? "var(--ok)"
            : "repeating-linear-gradient(90deg, var(--warnf) 0 6px, transparent 6px 10px)"
        }
      >
        {exported
          ? `Exported ${Object.keys(exported.masters).join(", ")} just now`
          : complete
            ? "Every chunk has a take in the cut"
            : `Waiting on chunk ${missing.map((e) => e.chunk_ordinal).join(", ")}`}
      </Step>

      <Step icon="🚀" name="Publish" state="next" tone="faint" bar="var(--off)">
        {chapters
          ? `${
              chapters.entries.length === 0
                ? "No chapters named yet"
                : `${chapters.entries.length} chapter${chapters.entries.length === 1 ? "" : "s"} ${
                    chapters.usable ? "ready" : "need fixing"
                  }`
            } · ${publish?.data.title.chosen ? "title chosen" : "no title yet"}`
          : "Chapters and title come next"}
      </Step>
    </ol>
  );
}

function Step({
  icon,
  name,
  state,
  done,
  tone,
  bar,
  children,
}: {
  icon: string;
  name: string;
  state: string;
  done?: boolean;
  tone?: "ok" | "warn" | "bad" | "faint";
  bar: string;
  children: React.ReactNode;
}) {
  return (
    <li className="step">
      <div className="step-head">
        <span aria-hidden>{icon}</span>
        <span className="step-name">{name}</span>
        <span className={done ? "step-state done" : `step-state ${tone ?? ""}`}>
          {done ? (
            <>
              <span aria-hidden>✓</span>
              <span className="sr-only">done</span>
            </>
          ) : (
            state
          )}
        </span>
      </div>
      <div className="step-bar" aria-hidden style={{ background: bar }} />
      <span className="step-text">{children}</span>
    </li>
  );
}

/** A bar in proportional segments, drawn as one gradient. */
function split(parts: [number, string][]): string {
  const sum = parts.reduce((s, [n]) => s + n, 0);
  if (!sum) return "var(--off)";
  let at = 0;
  const stops = parts
    .filter(([n]) => n > 0)
    .map(([n, colour]) => {
      const from = (at / sum) * 100;
      at += n;
      return `${colour} ${from}% ${(at / sum) * 100}%`;
    });
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}
