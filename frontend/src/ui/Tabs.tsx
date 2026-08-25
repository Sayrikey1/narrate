import { useRef } from "react";

/** A tab strip that a keyboard can drive.
 *
 *  The Plan page grew a pair of `<button role="tab">` by hand, which is the
 *  point at which it becomes worth doing properly: a `tablist` is one tab stop
 *  with arrow keys inside it, not one stop per tab, and the panel has to be
 *  associated with its tab for a screen reader to follow the switch.
 */

export interface Tab<T extends string> {
  id: T;
  label: string;
  /** Shown after the label, for a count. */
  hint?: string;
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: readonly Tab<T>[];
  active: T;
  onChange: (id: T) => void;
  /** Names the group for assistive technology. */
  label: string;
}) {
  const strip = useRef<HTMLDivElement | null>(null);

  function onKeyDown(event: React.KeyboardEvent, index: number) {
    const step =
      event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    let next = -1;
    if (step) next = (index + step + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    if (next < 0) return;

    event.preventDefault();
    const target = tabs[next];
    if (!target) return;
    onChange(target.id);
    strip.current?.querySelectorAll<HTMLElement>("[role='tab']")[next]?.focus();
  }

  return (
    <div className="tabs" role="tablist" aria-label={label} ref={strip}>
      {tabs.map((tab, index) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`panel-${tab.id}`}
            // Roving tabindex: the strip is one stop, the arrows move inside it.
            tabIndex={selected ? 0 : -1}
            className={selected ? "tab on" : "tab"}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            {tab.label}
            {tab.hint && <span className="tab-hint">{tab.hint}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** The region a tab reveals. Kept separate so a page can put it anywhere. */
export function TabPanel({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`}>
      {children}
    </div>
  );
}
