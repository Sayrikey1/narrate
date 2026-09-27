import { useEffect, useRef, useState } from "react";
import type { DeleteBody, DeletePlan } from "../types";

/** Deleting an episode or a project — what goes and what stays, then confirm.
 *
 *  Opening the panel asks the server what the delete would do, and changes
 *  nothing. The spend always stays on the record: deleting hides the episode
 *  and stops it spending, it does not un-spend it. Files on disk go only if the
 *  box is ticked, and only files nothing else still uses. */
export function DeletePanel({
  what,
  run,
  onDone,
  onClose,
}: {
  /** "episode" or "project", for the wording. */
  what: string;
  run: (body: DeleteBody) => Promise<DeletePlan>;
  onDone: (plan: DeletePlan) => void;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<DeletePlan | null>(null);
  const [files, setFiles] = useState(false);
  // A run recorded as "running" that crashed never finishes on its own; this
  // is the way past it, stated plainly.
  const [force, setForce] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    run({})
      .then(setPlan)
      .catch((e) => setError(String(e)));
    // Once, when the panel opens: the preview is of what exists now.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (plan) first.current?.focus();
  }, [plan]);

  async function confirm() {
    setWorking(true);
    setError(null);
    try {
      onDone(await run({ confirm: true, delete_files: files, ...(force ? { force } : {}) }));
    } catch (e) {
      setError(String(e));
    } finally {
      setWorking(false);
    }
  }

  const mb = plan ? (plan.file_bytes / 1_000_000).toFixed(1) : "0";
  return (
    <div className="regen" role="group" aria-label={`Delete this ${what}`}>
      {error && <div className="note error">{error}</div>}
      {!plan && !error && <p className="dim">Checking what this would delete…</p>}
      {plan && (
        <>
          <p>
            Deletes the {what} <strong>{plan.name}</strong>
            {plan.kind === "project" ? `, with ${plan.episodes.length} episode(s)` : ""} —{" "}
            {plan.takes} take(s).
          </p>
          <p>
            The <strong>{plan.spend_usd}</strong> it cost stays on the record, attributed to it,
            in every total. It can be restored from the Costs page.
          </p>
          {plan.running.length > 0 && (
            <>
              <div className="note warn">
                A generation looks like it is still running. Wait for it to finish before deleting.
              </div>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={force}
                  disabled={working}
                  onChange={(e) => setForce(e.target.checked)}
                />
                It crashed and will never finish — delete anyway
              </label>
            </>
          )}
          <label className="checkbox">
            <input
              type="checkbox"
              checked={files}
              disabled={working}
              onChange={(e) => setFiles(e.target.checked)}
            />
            Also delete its {plan.files} file(s) from disk ({mb} MB) — they cannot be restored
          </label>
          <div className="regen-actions">
            <button
              className="danger"
              disabled={working || (plan.running.length > 0 && !force)}
              onClick={confirm}
            >
              {working ? "Deleting…" : `Delete ${what}`}
            </button>
            {/* Focus lands here, not on the delete: pressing Enter twice must
                not confirm a delete nobody chose. */}
            <button ref={first} className="ghost" disabled={working} onClick={onClose}>
              Cancel
            </button>
          </div>
        </>
      )}
    </div>
  );
}
