import { useEffect, useState } from "react";
import { api } from "../api";
import type { ActivityEvent } from "../types";

const FIRST = 6;

/** What has happened to the episode, newest first: runs, checks, exports,
 *  drafts. Read back from its records, so it covers work done before this
 *  card existed, and from the command line. */
export function History({ scriptId, refreshKey }: { scriptId: number; refreshKey: unknown }) {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  const [all, setAll] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .activity(scriptId)
      .then((e) => alive && setEvents(e))
      .catch(() => alive && setEvents([]));
    return () => {
      alive = false;
    };
  }, [scriptId, refreshKey]);

  if (!events?.length) return null;
  const shown = all ? events : events.slice(0, FIRST);

  return (
    <section className="panel" aria-labelledby="history-h">
      <div className="card-head">
        <h2 id="history-h" className="card-title">
          🕘 History
        </h2>
      </div>
      <ol className="history">
        {shown.map((event, i) => (
          <li key={`${event.at}-${i}`}>
            <span className="history-icon" aria-hidden>
              {event.icon}
            </span>
            <span className="history-text">
              <span>{event.text}</span>
              <time className="faint" dateTime={event.at}>
                {when(event.at)}
              </time>
            </span>
          </li>
        ))}
      </ol>
      {events.length > FIRST && (
        <button type="button" className="small ghost" onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${events.length}`}
        </button>
      )}
    </section>
  );
}

/** "today 14:05", "yesterday 09:12", "Sat 3 Oct 18:25". */
function when(iso: string): string {
  const at = new Date(iso);
  const time = at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (at.toDateString() === today.toDateString()) return `today ${time}`;
  if (at.toDateString() === yesterday.toDateString()) return `yesterday ${time}`;
  return `${at.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })} ${time}`;
}
