import type { ChunkRow } from "../types";
import { api, usd } from "../api";
import { CheckIcon } from "./Icon";
import { Player } from "./Player";

/** Chunks with their takes. Auditioning and promoting a take into the cut is
 *  the loop this screen exists for. */
export function ChunkList({
  chunks,
  scriptId,
  onChange,
  busy,
}: {
  chunks: ChunkRow[];
  scriptId: number;
  onChange: () => void;
  busy: boolean;
}) {
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

  return (
    <table>
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
          <tr key={chunk.ordinal}>
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
                <div key={take.id} className="take-row">
                  <span className={take.in_cut ? "tag cut" : "tag"}>
                    {take.in_cut && <CheckIcon />}
                    take {take.ordinal}
                  </span>
                  <span className="faint mono">
                    {take.duration_s ? `${take.duration_s.toFixed(2)}s · ` : ""}
                    {usd(take.cost_micros)}
                  </span>
                  <Player src={api.audioTake(take.id)} duration={take.duration_s} />
                  {!take.in_cut && take.status === "succeeded" && (
                    <button
                      className="small"
                      disabled={busy}
                      onClick={() => promote(chunk.ordinal, take.ordinal)}
                    >
                      use this
                    </button>
                  )}
                </div>
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
