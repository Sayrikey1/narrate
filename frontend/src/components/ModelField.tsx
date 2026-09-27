import { useEffect } from "react";
import { SelectField } from "../ui/form";
import { Notice } from "../ui/feedback";
import type { Model } from "../types";

/** Pick a model, with its rate and its limits at the point of choice.
 *
 *  The PRD asks for this specifically: "the 2× price difference between tiers
 *  should be visible at the point of choice". The label carries the rate and
 *  the request ceiling, and anything the model *cannot* do is stated below it
 *  rather than discovered when a seam turns up in the finished audio.
 */
export function ModelField({
  models,
  value,
  onChange,
  autoSelect,
}: {
  models: Model[];
  value: string;
  onChange: (id: string) => void;
  /** Preselect the server's default model once the list arrives. */
  autoSelect?: boolean;
}) {
  const model = models.find((m) => m.model_id === value);

  useEffect(() => {
    if (!autoSelect || value || !models.length) return;
    // The server says which model is the default, so the form, the CLI and
    // the API cannot disagree about it.
    const chosen = models.find((m) => m.default) ?? models[0];
    if (chosen) onChange(chosen.model_id);
  }, [autoSelect, models, value, onChange]);

  return (
    <>
      <SelectField
        label="Model"
        value={value}
        onChange={onChange}
        options={[
          // A project on a model no longer offered (a deprecated one) must show
          // that model, not whichever option happens to come first — or the
          // form would display one model and save another.
          ...(value && !model ? [{ value, label: `${value} (no longer offered — choose another)` }] : []),
          ...models.map((m) => ({
            value: m.model_id,
            label:
              `${m.label} — $${m.usd_per_1k.toFixed(2)}/1k, ${m.max_chars.toLocaleString()} chars` +
              (m.default ? " (default)" : ""),
          })),
        ]}
      />
      {model?.continuity_mode === "none" && (
        <Notice tone="warn">
          {model.label} has no cross-chunk continuity — neither request stitching
          nor text conditioning — so seams between chunks are unavoidable. For
          one long narrator where that matters most, Multilingual v2 stitches.
        </Notice>
      )}
      {model?.dialogue && (
        <Notice tone="ok">
          {model.label} can render several speakers in one request. Cast the
          project, then add the script from the command line with{" "}
          <code>narrate script add … --dialogue</code>.
        </Notice>
      )}
      {model?.note && <Notice>{model.note}</Notice>}
    </>
  );
}
