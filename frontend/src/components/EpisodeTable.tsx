import { useEffect, useState } from "react";
import { api, usd } from "../api";
import { Link, scriptPath } from "../router";
import type { Cost, ScriptSummary } from "../types";

/** The project's episodes, each with what is left to generate and what it has
 *  cost so far. */
export function EpisodeTable({
  projectName,
  episodes,
}: {
  projectName: string;
  episodes: ScriptSummary[];
}) {
  const costs = useCosts(episodes);

  return (
    <section className="panel" aria-labelledby="eps-h">
      <div className="card-head">
        <h2 id="eps-h">
          Episodes in {projectName} <span className="count">{episodes.length}</span>
        </h2>
      </div>
      {episodes.length === 0 ? (
        <div className="empty">
          <strong>No episodes yet</strong>
          Add one with the card on the right — drop a .txt or .md file, or paste the text.
        </div>
      ) : (
        <div className="table-scroll">
          <table className="episodes">
            <caption className="sr-only">
              Episodes in {projectName}, with what is left to generate and what each has cost
            </caption>
            <thead>
              <tr>
                <th scope="col">Episode</th>
                <th scope="col">Status</th>
                <th scope="col" className="num">
                  Spent
                </th>
                <th scope="col">
                  <span className="sr-only">Open</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {episodes.map((episode) => {
                const cost = costs[episode.id];
                const status = cost === undefined ? null : describe(episode, cost);
                return (
                  <tr key={episode.id}>
                    <td>
                      <Link className="episode-cell" to={scriptPath(episode.id)} title={episode.title}>
                        <span className="icon-tile sz-42" aria-hidden>
                          🎙️
                        </span>
                        <span className="episode-cell-text">
                          <span className="episode-cell-title">{episode.title}</span>
                          <span className="episode-cell-meta">
                            {episode.chunks} chunk{episode.chunks === 1 ? "" : "s"} ·{" "}
                            {episode.chars.toLocaleString()} chars
                          </span>
                        </span>
                      </Link>
                    </td>
                    <td className={status?.tone ?? "faint"}>{status?.text ?? "…"}</td>
                    <td className="num mono dim">
                      {cost ? usd(cost.cost_micros) : cost === null ? "—" : "…"}
                    </td>
                    <td className="num">
                      <Link className="btn small" to={scriptPath(episode.id)}>
                        Open<span className="sr-only"> {episode.title}</span> →
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Each episode's cost report, fetched together. `null` for one that failed:
 *  its row says "—" rather than holding the rest of the table back. */
function useCosts(episodes: ScriptSummary[]): Record<number, Cost | null> {
  const [costs, setCosts] = useState<Record<number, Cost | null>>({});
  const ids = episodes.map((e) => e.id).join(",");

  useEffect(() => {
    let alive = true;
    for (const id of ids ? ids.split(",").map(Number) : []) {
      api
        .cost(id)
        .then((cost) => alive && setCosts((all) => ({ ...all, [id]: cost })))
        .catch(() => alive && setCosts((all) => ({ ...all, [id]: null })));
    }
    return () => {
      alive = false;
    };
  }, [ids]);

  return costs;
}

function describe(
  episode: ScriptSummary,
  cost: Cost | null,
): { text: string; tone: string } {
  if (cost === null) return { text: "—", tone: "faint" };
  const todo = cost.outstanding;
  const about = todo ? ` — about ${usd(todo.cost_micros, 2)}` : "";
  if (!todo || (todo.chunks === 0 && todo.effects === 0)) {
    return { text: "✅ Every chunk has audio", tone: "ok" };
  }
  if (cost.cost_micros === 0 && todo.chunks >= episode.chunks) {
    return { text: `📝 Script only${about} to generate`, tone: "dim" };
  }
  if (todo.chunks > 0) {
    return {
      text: `🎙️ ${todo.chunks} of ${episode.chunks} chunk${episode.chunks === 1 ? "" : "s"} to generate${about}`,
      tone: "warn",
    };
  }
  return {
    text: `✨ ${todo.effects} effect${todo.effects === 1 ? "" : "s"} to generate${about}`,
    tone: "warn",
  };
}
