import type { Cost } from "../types";
import { usd } from "../api";

const KIND_LABEL: Record<string, string> = {
  generation: "🎙️ Narration",
  effect: "✨ Effects",
  suggestion: "🪄 Suggestions",
  probe: "🔬 Probes",
  copy: "✍️ Copywriting",
  correction: "↩️ Corrections",
};

/** Spend, broken down by what it bought.
 *
 *  Both units are shown because the account may be billed either way — dollars
 *  on the API, credits on a subscription — and a figure recorded in only one of
 *  them stops being comparable the moment the billing mode changes. */
export function CostPanel({ cost, projectName }: { cost: Cost | null; projectName?: string }) {
  if (!cost) return null;

  const capPct = Math.min(100, cost.cap_used_pct ?? 0);
  const capTone = capPct >= 100 ? " danger" : capPct >= 80 ? " warn" : "";
  const outstanding = cost.outstanding;
  const hasOutstanding = !!outstanding && (outstanding.chunks > 0 || outstanding.effects > 0);

  return (
    <>
      <table className="cost-table">
        <caption className="sr-only">Spend on this episode by kind</caption>
        <tbody>
          {cost.by_kind.map((kind) => (
            <tr key={`${kind.kind}-${kind.unit_kind}`}>
              <th scope="row">{KIND_LABEL[kind.kind] ?? kind.kind}</th>
              <td className="unit">{kind.units_display}</td>
              <td>{usd(kind.cost_micros)}</td>
            </tr>
          ))}
          <tr className="total">
            <th scope="row">Spent</th>
            <td className="unit">
              {cost.credits ? `${Math.round(cost.credits).toLocaleString()} cr` : ""}
            </td>
            <td>{usd(cost.cost_micros)}</td>
          </tr>
          {hasOutstanding && (
            <>
              <tr className="soft">
                <th scope="row">Still to generate</th>
                <td className="unit">
                  {[
                    outstanding!.chunks > 0 &&
                      `${outstanding!.chunks} chunk${outstanding!.chunks === 1 ? "" : "s"}`,
                    outstanding!.effects > 0 &&
                      `${outstanding!.effects} effect${outstanding!.effects === 1 ? "" : "s"}`,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </td>
                <td>~{usd(outstanding!.cost_micros)}</td>
              </tr>
              <tr className="soft">
                <th scope="row">Projected total</th>
                <td />
                <td>{usd(cost.projected_micros ?? cost.cost_micros)}</td>
              </tr>
            </>
          )}
        </tbody>
      </table>

      <div className="tiles">
        <Tile label="In the cut" value={usd(cost.selected_micros)} />
        <Tile label="Re-rolls" value={usd(cost.wasted_micros)} />
        <Tile
          label="Waste ratio"
          value={`${cost.waste_pct}%`}
          tone={cost.waste_pct > 25 ? "warn" : undefined}
        />
        <Tile
          label="Per minute"
          value={cost.cost_per_minute_micros ? usd(cost.cost_per_minute_micros) : "—"}
        />
      </div>

      {cost.cap_micros != null && (
        <div className="meter-block">
          <div className="meter-head">
            <span>{projectName ? `${projectName} monthly cap` : "Monthly cap"}</span>
            <span>
              {usd(cost.cap_micros, 2)} · {cost.cap_used_pct}%
            </span>
          </div>
          <div className={`meter${capTone}`} aria-hidden>
            <div style={{ width: `${capPct}%` }} />
          </div>
        </div>
      )}

      <p className="note flush">
        Waste is spend on takes that never made the cut — the number that says whether the
        direction is working, not the voice.
      </p>
    </>
  );
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: "warn" }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className={tone ? `tile-value ${tone}` : "tile-value"}>{value}</span>
    </div>
  );
}
