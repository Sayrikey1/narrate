import type { Cost } from "../types";
import { usd } from "../api";

const KIND_LABEL: Record<string, string> = {
  generation: "narration",
  effect: "effects",
  suggestion: "suggestions",
  probe: "probes",
};

/** Spend, broken down by what it bought.
 *
 *  Both units are shown because the account may be billed either way — dollars
 *  on the API, credits on a subscription — and a figure recorded in only one of
 *  them stops being comparable the moment the billing mode changes. */
export function CostPanel({ cost }: { cost: Cost | null }) {
  if (!cost) return null;

  const capPct = Math.min(100, cost.cap_used_pct ?? 0);
  const capClass = capPct >= 100 ? "bar danger" : capPct >= 80 ? "bar warn" : "bar";
  const outstanding = cost.outstanding;
  const hasOutstanding = !!outstanding && (outstanding.chunks > 0 || outstanding.effects > 0);

  return (
    <>
      {cost.by_kind.length > 0 && (
        <table className="cost-kinds">
          <tbody>
            {cost.by_kind.map((kind) => (
              <tr key={`${kind.kind}-${kind.unit_kind}`}>
                <td>{KIND_LABEL[kind.kind] ?? kind.kind}</td>
                <td className="num dim mono">{kind.units_display}</td>
                <td className="num mono">{usd(kind.cost_micros)}</td>
                <td className="num dim mono">
                  {kind.credits ? `${Math.round(kind.credits).toLocaleString()} cr` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="stat">
        <span className="dim">Spent</span>
        <span>
          {usd(cost.cost_micros)}
          {cost.credits ? (
            <span className="dim"> · {Math.round(cost.credits).toLocaleString()} cr</span>
          ) : null}
        </span>
      </div>

      {hasOutstanding && (
        <>
          <div className="stat">
            <span className="dim">Outstanding</span>
            <span>~{usd(outstanding!.cost_micros)}</span>
          </div>
          <div className="stat">
            <span className="dim">Projected</span>
            <span>{usd(cost.projected_micros ?? cost.cost_micros)}</span>
          </div>
          <p className="note">
            {outstanding!.chunks > 0 &&
              `${outstanding!.chunks} chunk${outstanding!.chunks === 1 ? "" : "s"} `}
            {outstanding!.effects > 0 &&
              `${outstanding!.effects} effect${outstanding!.effects === 1 ? "" : "s"} `}
            still to generate.
          </p>
        </>
      )}

      <div className="stat">
        <span className="dim">In the cut</span>
        <span>{usd(cost.selected_micros)}</span>
      </div>
      <div className="stat">
        <span className="dim">Re-rolls</span>
        <span>{usd(cost.wasted_micros)}</span>
      </div>
      <div className="stat">
        <span className="dim">Waste ratio</span>
        <span className={cost.waste_pct > 25 ? "warn" : ""}>{cost.waste_pct}%</span>
      </div>
      {cost.cost_per_minute_micros ? (
        <div className="stat">
          <span className="dim">Cost / minute</span>
          <span>{usd(cost.cost_per_minute_micros)}</span>
        </div>
      ) : null}

      {cost.cap_micros != null && (
        <div className="card-section">
          <div className="stat">
            <span className="dim">Monthly cap</span>
            <span>
              {usd(cost.cap_micros, 2)} · {cost.cap_used_pct}%
            </span>
          </div>
          <div className={capClass}>
            <div style={{ width: `${capPct}%` }} />
          </div>
        </div>
      )}

      <p className="note">
        Waste is spend on takes that never made the cut — the number that says whether the
        direction is working, not the voice.
      </p>
    </>
  );
}
