import type { ReactNode } from "react";

/** Layout primitives.
 *
 *  The class vocabulary these wrap already existed and was already consistent —
 *  `panel`, `page-head`, `row`/`grow`/`side`. What was missing is anything that
 *  *owns* it: seven pages each spelled out the same `<div className="panel">
 *  <h2>…` by hand, so a change to how a panel presents its heading meant seven
 *  edits, and the eighth page invented something slightly different.
 *
 *  Deliberately thin. These add structure and accessibility, not styling of
 *  their own; the tokens in `styles.css` remain the single source for that.
 */

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  /** Buttons and links for the page as a whole. */
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        <h1 className="page-title">{title}</h1>
        {subtitle && <span className="faint">{subtitle}</span>}
      </div>
      {actions && <div className="toolbar">{actions}</div>}
    </div>
  );
}

export function Card({
  title,
  actions,
  children,
  className,
}: {
  /** Rendered as the panel's small-caps heading. Omit for an unlabelled card. */
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={["panel", className].filter(Boolean).join(" ")}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="toolbar">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

/** A main column and an aside that stacks under it on a narrow window.
 *
 *  The shell has reflowed at 900px for a while; page *interiors* did not, so
 *  `.side` held a 300px floor and the content stayed cramped exactly when there
 *  was least room for it. */
export function Split({ main, aside }: { main: ReactNode; aside?: ReactNode }) {
  return (
    <div className="row">
      <div className="grow">{main}</div>
      {aside && <div className="side">{aside}</div>}
    </div>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>;
}
