import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

/** A very small router over the History API.
 *
 *  Forty lines instead of a dependency: this frontend's only packages are
 *  react and react-dom, and five routes do not justify changing that. The
 *  server has a catch-all returning the shell, so these paths survive a hard
 *  refresh.
 */

export interface Route {
  path: string;
  /** `/script/12/media` → `{ id: "12" }` */
  params: Record<string, string>;
  /** The matched pattern, e.g. `/script/:id/media`. */
  pattern: string | null;
}

const PATTERNS = [
  "/",
  "/cast",
  "/script/:id",
  "/script/:id/effects",
  "/script/:id/media",
  "/script/:id/plan",
  "/costs",
] as const;

export type Pattern = (typeof PATTERNS)[number];

function match(path: string): Route {
  const parts = path.replace(/\/+$/, "").split("/").filter(Boolean);

  for (const pattern of PATTERNS) {
    const wanted = pattern.split("/").filter(Boolean);
    if (wanted.length !== parts.length) continue;

    const params: Record<string, string> = {};
    let ok = true;
    for (const [i, segment] of wanted.entries()) {
      if (segment.startsWith(":")) params[segment.slice(1)] = parts[i];
      else if (segment !== parts[i]) {
        ok = false;
        break;
      }
    }
    if (ok) return { path, params, pattern };
  }
  return { path, params: {}, pattern: null };
}

const RouteContext = createContext<Route>({ path: "/", params: {}, pattern: "/" });

export function RouterProvider({ children }: { children: ReactNode }) {
  const [route, setRoute] = useState(() => match(window.location.pathname));

  useEffect(() => {
    const onPop = () => setRoute(match(window.location.pathname));
    window.addEventListener("popstate", onPop);
    // `navigate` dispatches this so a push updates every subscriber, which
    // popstate alone does not do.
    window.addEventListener("narrate:navigate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      window.removeEventListener("narrate:navigate", onPop);
    };
  }, []);

  return <RouteContext.Provider value={route}>{children}</RouteContext.Provider>;
}

export function useRoute(): Route {
  return useContext(RouteContext);
}

export function navigate(to: string, replace = false): void {
  if (to === window.location.pathname) return;
  if (replace) history.replaceState(null, "", to);
  else history.pushState(null, "", to);
  window.dispatchEvent(new Event("narrate:navigate"));
}

/** An anchor that routes client-side but stays a real link, so middle-click
 *  and "open in new tab" keep working. */
export function Link({
  to,
  children,
  className,
  title,
}: {
  to: string;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  const route = useRoute();
  const active = route.path === to || route.path.replace(/\/+$/, "") === to;

  const onClick = useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
      event.preventDefault();
      navigate(to);
    },
    [to],
  );

  return (
    <a
      href={to}
      onClick={onClick}
      title={title}
      className={[className, active ? "active" : ""].filter(Boolean).join(" ")}
      aria-current={active ? "page" : undefined}
    >
      {children}
    </a>
  );
}

export function scriptPath(id: number, tail = ""): string {
  return `/script/${id}${tail}`;
}
