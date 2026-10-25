import { useEffect, useState } from "react";
import { api, usd } from "../api";
import type { DraftKind, DraftQuote, Drafted } from "../types";

const NOUN: Record<DraftKind, [string, string]> = {
  titles: ["title", "titles"],
  briefs: ["thumbnail brief", "thumbnail briefs"],
  effects: ["suggestion", "suggestions"],
};

/** A paid draft, priced first: the button carries the estimate before it is
 *  pressed, and says afterwards what was made and what it actually cost.
 *  Without a Groq key it says how to get one rather than offering to spend. */
export function DraftButton({
  scriptId,
  kind,
  label,
  onDone,
  className,
}: {
  scriptId: number;
  kind: DraftKind;
  label: string;
  /** After a draft lands, so the page shows what it made. */
  onDone: () => void;
  className?: string;
}) {
  const [quote, setQuote] = useState<DraftQuote | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<Drafted | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .draftQuote(scriptId, kind)
      .then((q) => alive && setQuote(q))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [scriptId, kind]);

  if (!quote) return null;
  if (!quote.available) {
    return (
      <span className="faint draft-off" title="Drafting uses Groq: add GROQ_API_KEY to .env">
        🪄 {label} needs a Groq key
      </span>
    );
  }

  async function run() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const done = await api.draft(scriptId, kind);
      setResult(done);
      onDone();
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setRunning(false);
    }
  }

  const [one, many] = NOUN[kind];
  return (
    <span className={["draft", className].filter(Boolean).join(" ")}>
      <button type="button" className="small" disabled={running} onClick={() => void run()}>
        {running ? "Drafting…" : `🪄 ${label}`}{" "}
        {!running && <span className="mono faint">~{usd(quote.micros)}</span>}
      </button>
      {result && (
        <span className="faint draft-result" role="status">
          {result.stored
            ? `${result.stored} ${result.stored === 1 ? one : many} added, none chosen`
            : `No ${many} came back`}
          {result.description_written ? " · description and tags written" : ""} · cost{" "}
          {usd(result.cost_micros)}
        </span>
      )}
      {(error || result?.errors.length) && (
        <span className="error draft-result" role="alert">
          {error ?? result?.errors.join(" · ")}
        </span>
      )}
    </span>
  );
}
