import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

/** A button that opens a short list to pick one from: the project and episode
 *  switchers.
 *
 *  It is a `menu` of `menuitemradio`s rather than a native `<select>`, because
 *  each choice carries a second line — an episode's size, a project's spend —
 *  and a select cannot show one. So the keyboard behaviour is written out:
 *  ↓ on the button opens it on the current choice, ↑/↓ move, Home/End jump,
 *  Escape closes and hands focus back to the button, Tab closes and moves on.
 *  A click anywhere else closes it too.
 */

export interface MenuItem {
  key: string | number;
  label: string;
  /** A second, quieter line. */
  meta?: string;
  icon?: ReactNode;
  /** The choice in force, marked and focused when the menu opens. */
  current?: boolean;
  onSelect: () => void;
}

export function Menu({
  label,
  trigger,
  className,
  items,
  heading,
  footer,
  align = "left",
}: {
  /** Names the button and the list for assistive technology. */
  label: string;
  trigger: ReactNode;
  className?: string;
  items: readonly MenuItem[];
  heading?: string;
  /** One more action below the choices, such as "All projects". */
  footer?: MenuItem;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const root = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);
  const list = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const choices = list.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]');
    const current = list.current?.querySelector<HTMLElement>('[aria-checked="true"]');
    (current ?? choices?.[0])?.focus();

    const away = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  function close() {
    setOpen(false);
    button.current?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    const choices = [...(list.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [])];
    const at = choices.indexOf(document.activeElement as HTMLElement);
    let next = -1;
    if (event.key === "ArrowDown") next = (at + 1) % choices.length;
    else if (event.key === "ArrowUp") next = (at - 1 + choices.length) % choices.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = choices.length - 1;
    else if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    } else if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    if (next >= 0) {
      event.preventDefault();
      choices[next]?.focus();
    }
  }

  const choose = (item: MenuItem) => {
    setOpen(false);
    item.onSelect();
  };

  return (
    <div className="menu-anchor" ref={root}>
      <button
        ref={button}
        type="button"
        className={className}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={label}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        {trigger}
      </button>
      {open && (
        <div
          className={align === "right" ? "menu right" : "menu"}
          id={id}
          role="menu"
          aria-label={label}
          ref={list}
          onKeyDown={onKeyDown}
        >
          {heading && (
            <div className="menu-head" role="presentation">
              {heading}
            </div>
          )}
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              role="menuitemradio"
              aria-checked={item.current === true}
              className="menu-item"
              tabIndex={-1}
              onClick={() => choose(item)}
            >
              {item.icon}
              <span className="menu-item-text">
                <span>{item.label}</span>
                {item.meta && <span className="menu-item-meta">{item.meta}</span>}
              </span>
            </button>
          ))}
          {footer && (
            <div className="menu-foot" role="presentation">
              <button
                type="button"
                role="menuitem"
                className="menu-item"
                tabIndex={-1}
                onClick={() => choose(footer)}
              >
                {footer.icon}
                <span className="menu-item-text">
                  <span>{footer.label}</span>
                </span>
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
