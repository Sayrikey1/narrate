import type { Timeline, TimelineEntry } from "../types";

/** The running order as a table. Effects share a start time with the
 *  narration they sit over, because they are overlays — that is deliberate,
 *  not a rendering bug. */
export function TimelineView({ timeline }: { timeline: Timeline | null }) {
  if (!timeline) return <p className="faint">No script selected.</p>;
  if (!timeline.entries.length) {
    return (
      <div className="empty">
        <strong>Nothing on the timeline</strong>
        This script has no chunks yet.
      </div>
    );
  }

  const firstMissing = timeline.entries.find((e) => e.kind === "narration" && !e.generated);

  return (
    <div className="timing">
      <h3>⏱️ Running order</h3>
      <div className="table-scroll">
        <table>
          <caption className="sr-only">Every narration chunk and effect, in the order they play</caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Start</th>
              <th scope="col">End</th>
              <th scope="col" className="num">
                Length
              </th>
              <th scope="col">Track</th>
              <th scope="col">Content</th>
            </tr>
          </thead>
          <tbody>
            {timeline.entries.map((entry) => (
              <Row key={entry.index} entry={entry} />
            ))}
          </tbody>
        </table>
      </div>
      {firstMissing && (
        <p className="note warn flush">
          ⏳ Timings after the first ungenerated chunk ({firstMissing.chunk_ordinal}) are provisional.
        </p>
      )}
    </div>
  );
}

function Row({ entry }: { entry: TimelineEntry }) {
  const narration = entry.kind === "narration";
  const kind = narration
    ? entry.generated
      ? { label: "narration", className: "kind" }
      : { label: "no audio", className: "kind failed" }
    : entry.generated
      ? { label: "effect", className: "kind effect" }
      : { label: "planned", className: "kind planned" };

  return (
    <tr>
      <td className="mono dim">{narration ? entry.chunk_ordinal : "✨"}</td>
      <td className="mono">{entry.start}</td>
      <td className="mono dim">{entry.end}</td>
      <td className="num mono">{entry.duration_s.toFixed(1)}s</td>
      <td>
        <span className={kind.className}>{kind.label}</span>
      </td>
      <td className={narration ? undefined : "dim"}>{narration ? entry.label : <em>{entry.label}</em>}</td>
    </tr>
  );
}
