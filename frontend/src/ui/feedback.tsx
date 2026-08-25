import type { ReactNode } from "react";

/** Telling somebody what is going on.
 *
 *  The gap these fill is the one that made the app feel dead: a page that is
 *  loading looked identical to a page with nothing in it, and a page that had
 *  *failed* looked like both. `<p className="dim">Loading…</p>` was the whole
 *  vocabulary, and several screens did not even have that.
 */

/** Nothing here yet — with the reason, and what to do about it.
 *
 *  An empty state that only says "no results" makes the reader wonder whether
 *  something is broken. Every one of these says what would fill it. */
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children}
      {action && <div className="toolbar empty-action">{action}</div>}
    </div>
  );
}

/** A grey block standing in for content that is on its way.
 *
 *  Preferred over a spinner for anything with a known shape: the layout does
 *  not jump when the data lands, which is most of what makes loading feel
 *  cheap. `aria-hidden` because a screen reader should hear the live region
 *  below, not a description of grey rectangles. */
export function Skeleton({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={["skeleton", className].filter(Boolean).join(" ")} aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton-row" style={{ width: `${92 - i * 11}%` }} />
      ))}
    </div>
  );
}

/** The three states of a fetch, decided once.
 *
 *  Every page had its own `if (loading) … if (error) …` ladder, and they
 *  disagreed: some showed the error, some swallowed it, one showed an empty
 *  state for a failed request — which reads as "there is nothing" when it
 *  means "we could not find out". */
export function Loadable({
  loading,
  error,
  children,
  rows,
  onRetry,
}: {
  loading: boolean;
  error?: string | null;
  /** Omit to use this purely as a loading/error gate. */
  children?: ReactNode;
  rows?: number;
  onRetry?: () => void;
}) {
  if (error) {
    return (
      <Notice tone="error" title="That did not load">
        {error}
        {onRetry && (
          <button className="ghost small" onClick={onRetry}>
            Try again
          </button>
        )}
      </Notice>
    );
  }
  if (loading) {
    return (
      <div role="status" aria-live="polite">
        <span className="sr-only">Loading</span>
        <Skeleton rows={rows} />
      </div>
    );
  }
  return <>{children}</>;
}

export type Tone = "info" | "warn" | "error" | "ok";

/** A short message with a reason. `note` and `error` were separate class names
 *  applied inconsistently; this makes the tone a parameter. */
export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: Tone;
  title?: string;
  children: ReactNode;
}) {
  return (
    <div className={`notice ${tone}`} role={tone === "error" ? "alert" : undefined}>
      {title && <strong>{title}</strong>}
      <span>{children}</span>
    </div>
  );
}

export function Badge({ tone, children }: { tone?: Tone | "effect" | "planned"; children: ReactNode }) {
  return <span className={["tag", tone].filter(Boolean).join(" ")}>{children}</span>;
}

/** A label and a figure on one line. Used down the side panels, where the
 *  numbers are the content. */
export function Stat({
  label,
  value,
  tone,
  total,
}: {
  label: ReactNode;
  value: ReactNode;
  tone?: Tone;
  /** Emphasised, for the line a reader is looking for. */
  total?: boolean;
}) {
  return (
    <div className={total ? "stat total" : "stat"}>
      <span>{label}</span>
      <span className={tone}>{value}</span>
    </div>
  );
}
