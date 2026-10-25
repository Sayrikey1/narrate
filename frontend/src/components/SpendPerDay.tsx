import { useState } from "react";
import { usd } from "../api";
import type { Project, Spend } from "../types";

/** This month's spend, a column a day, across every project.
 *
 *  One series, so no legend: the title names it. Each day is focusable and
 *  carries its own figure, the highest day is labelled on its cap, and the
 *  numbers are in a table underneath for anyone who would rather read than
 *  point. */
export function SpendPerDay({ spend, projects }: { spend: Spend; projects: Project[] }) {
  const [active, setActive] = useState<number | null>(null);
  const month = new Date(`${spend.month_start}T00:00:00Z`).toLocaleString("en-GB", {
    month: "long",
    timeZone: "UTC",
  });
  const days = spend.days;
  const most = Math.max(0, ...days.map((d) => d.micros));
  const top = niceTop(most);
  const ticks = [top, top / 2, 0];
  const peak = most > 0 ? days.findIndex((d) => d.micros === most) : -1;
  const name = new Map(projects.map((p) => [String(p.id), p.name]));
  const at = (micros: number) => `${top ? (micros / top) * 100 : 0}%`;
  const shown = active === null ? null : days[active];

  return (
    <section className="panel" aria-labelledby="daily-h">
      <div className="card-head">
        <h2 id="daily-h" className="card-title">
          📈 Spend per day, {month}
        </h2>
        <span className="card-sub flush">All projects · hover or tab to a day</span>
      </div>
      <div className="daily">
        <div className="daily-axis mono" aria-hidden>
          {ticks.map((t) => (
            <span key={t} style={{ bottom: at(t) }}>
              {usd(t, 2)}
            </span>
          ))}
        </div>
        <div className="daily-plot">
          <div className="daily-grid" aria-hidden>
            {ticks.map((t) => (
              <span key={t} style={{ bottom: at(t) }} />
            ))}
          </div>
          <div className="daily-cols" role="group" aria-label={`Spend per day in ${month}`}>
            {days.map((d, i) => (
              <button
                key={d.date}
                type="button"
                className={i === active ? "daily-col on" : "daily-col"}
                aria-label={`${dayLabel(d.date)}: ${usd(d.micros, 2)}`}
                onMouseEnter={() => setActive(i)}
                onMouseLeave={() => setActive(null)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
              >
                {d.micros > 0 && <span className="daily-bar" style={{ height: at(d.micros) }} />}
                {i === peak && (
                  <span className="daily-peak mono" style={{ bottom: at(d.micros) }} aria-hidden>
                    {usd(d.micros, 2)}
                  </span>
                )}
              </button>
            ))}
          </div>
          {shown && active !== null && (
            <div
              className="daily-tip"
              role="status"
              style={{ left: `${((active + 0.5) / days.length) * 100}%`, bottom: `calc(${at(shown.micros)} + 14px)` }}
            >
              <strong className="mono">{usd(shown.micros, 2)}</strong>
              <span>{dayLabel(shown.date)}</span>
              {Object.keys(shown.by_project).length > 1 && (
                <span className="daily-tip-note">
                  {Object.entries(shown.by_project)
                    .sort((a, b) => b[1] - a[1])
                    .map(([id, m]) => `${name.get(id) ?? "a deleted project"} ${usd(m, 2)}`)
                    .join(" · ")}
                </span>
              )}
            </div>
          )}
          <div className="daily-x mono" aria-hidden>
            {days.map((d, i) => {
              const n = Number(d.date.slice(8));
              return n === 1 || n % 5 === 0 || i === days.length - 1 ? (
                <span key={d.date} style={{ left: `${((i + 0.5) / days.length) * 100}%` }}>
                  {n}
                </span>
              ) : null;
            })}
          </div>
        </div>
      </div>
      <details className="daily-table">
        <summary>📋 Show the numbers as a table</summary>
        <div className="mono">
          {days.map((d) => (
            <span key={d.date}>
              {dayLabel(d.date)} · {usd(d.micros, 2)}
            </span>
          ))}
        </div>
      </details>
    </section>
  );
}

/** A round top for the scale: the smallest of 1, 2 or 5 times a power of ten
 *  — in dollars — that the highest day fits under. */
export function niceTop(micros: number): number {
  if (micros <= 0) return 1_000_000;
  const dollars = micros / 1_000_000;
  const power = 10 ** Math.floor(Math.log10(dollars));
  const step = [1, 2, 5, 10].find((m) => m * power >= dollars) ?? 10;
  return Math.round(step * power * 1_000_000);
}

/** "Mon 12 Oct". */
function dayLabel(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}
