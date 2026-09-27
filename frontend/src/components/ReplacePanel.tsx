import { useRef, useState } from "react";
import { api } from "../api";
import type { ReplacePlan } from "../types";

/** Replacing an episode's script — priced before anything changes.
 *
 *  Chunks whose words did not change keep their takes, so an edited script pays
 *  only for what was edited; chunks the new script drops keep their paid takes
 *  in an archived copy. "Price it" applies nothing: it asks what would be kept,
 *  added and dropped, and what generating the new parts would cost. Choosing a
 *  different file or text throws the price away. */
export function ReplacePanel({
  scriptId,
  busy,
  onDone,
  onClose,
}: {
  scriptId: number;
  busy: boolean;
  onDone: (plan: ReplacePlan) => void;
  onClose: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [quote, setQuote] = useState<ReplacePlan | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [force, setForce] = useState(false);
  // Sticks once a refusal said a run is still going: ticking the box clears
  // that error, and the box must not vanish with it.
  const [offerForce, setOfferForce] = useState(false);
  // Bumped on every change, so a price that arrives after the input moved on
  // is dropped rather than shown for something else.
  const version = useRef(0);

  function changed() {
    version.current += 1;
    setQuote(null);
    setError(null);
  }

  const send = (confirm: boolean) =>
    file
      ? api.replaceScriptUpload(scriptId, file, confirm, force)
      : api.replaceScript(scriptId, { text, confirm, ...(force ? { force } : {}) });

  function refused(message: string) {
    setError(message);
    if (message.includes("still running")) setOfferForce(true);
  }

  async function price() {
    const asked = version.current;
    setWorking(true);
    setError(null);
    try {
      const answer = await send(false);
      if (asked === version.current) setQuote(answer);
    } catch (e) {
      if (asked === version.current) refused(String(e));
    } finally {
      setWorking(false);
    }
  }

  async function replace() {
    setWorking(true);
    setError(null);
    try {
      onDone(await send(true));
    } catch (e) {
      refused(String(e));
    } finally {
      setWorking(false);
    }
  }

  const ready = Boolean(file) || text.trim().length > 0;
  return (
    <div className="regen" role="group" aria-label="Replace this episode's script">
      <div className="regen-options">
        <label>
          New script file
          <input
            type="file"
            accept=".md,.markdown,.txt,text/markdown,text/plain"
            disabled={busy || working}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              changed();
            }}
          />
        </label>
        {!file && (
          <textarea
            className="regen-text"
            rows={6}
            value={text}
            disabled={busy || working}
            placeholder="…or paste the new script here"
            aria-label="New script text"
            onChange={(e) => {
              setText(e.target.value);
              changed();
            }}
          />
        )}
      </div>

      {error && <div className="note error">{error}</div>}
      {offerForce && (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={force}
            disabled={working}
            onChange={(e) => {
              setForce(e.target.checked);
              changed();
            }}
          />
          It crashed and will never finish — replace anyway
        </label>
      )}
      {quote?.unchanged && <p className="dim">That script is the same as the current one.</p>}
      {quote && !quote.unchanged && (
        <div className="regen-quote">
          <p>
            Keeps <strong>{quote.kept}</strong> chunk(s) with their {quote.kept_takes} take(s)
            {quote.kept_regenerated > 0 &&
              ` (${quote.kept_regenerated} generated again: a neighbour changed and this model carries context)`}
            ; adds <strong>{quote.new}</strong>
            {quote.retired > 0 &&
              `; drops ${quote.retired}${quote.retired_takes ? `, keeping their ${quote.retired_takes} paid take(s) in an archived copy` : ""}`}
            .
          </p>
          <p>
            Generating what changed afterwards: {quote.chunks_to_generate} chunk(s)
            {quote.effects_to_generate > 0 ? `, ${quote.effects_to_generate} effect(s)` : ""} —
            about <strong>{quote.quote_usd}</strong>. Replacing sends nothing.
          </p>
          {quote.slots_dropped > 0 && (
            <p className="dim">
              Removes {quote.slots_dropped} effect slot(s) placed on passages the new script
              drops; their sounds stay in the effects library.
            </p>
          )}
          {quote.beats_detached > 0 && (
            <p className="dim">Its outline ({quote.beats_detached} beat(s)) is detached.</p>
          )}
          {quote.warnings.map((w) => (
            <p key={w} className="dim">
              {w}
            </p>
          ))}
        </div>
      )}

      <div className="regen-actions">
        {!quote || quote.unchanged ? (
          <button disabled={busy || working || !ready} onClick={price}>
            {working ? "Pricing…" : "Price it"}
          </button>
        ) : (
          <button className="primary" disabled={busy || working} onClick={replace}>
            {working ? "Replacing…" : "Replace the script"}
          </button>
        )}
        <button className="ghost" disabled={working} onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
