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
  /** Choose the best long-form model once the list arrives. */
  autoSelect?: boolean;
}) {
  const model = models.find((m) => m.model_id === value);

  useEffect(() => {
    if (!autoSelect || value || !models.length) return;
    // Default to the model rated best for long-form work, which is the one
    // where request stitching keeps prosody continuous across chunks.
    const best = models.find((m) => m.long_form === "best") ?? models[0];
    if (best) onChange(best.model_id);
  }, [autoSelect, models, value, onChange]);

  return (
    <>
      <SelectField
        label="Model"
        value={value}
        onChange={onChange}
        options={models.map((m) => ({
          value: m.model_id,
          label: `${m.label} — $${m.usd_per_1k.toFixed(2)}/1k, ${m.max_chars.toLocaleString()} chars`,
        }))}
      />
      {model?.continuity_mode === "none" && (
        <Notice tone="warn">
          {model.label} has no cross-chunk continuity — neither request stitching
          nor text conditioning — so seams between chunks are unavoidable.
        </Notice>
      )}
      {model?.dialogue && (
        <Notice tone="ok">
          {model.label} can render several speakers in one request. Cast the
          project and add <code>--dialogue</code> when ingesting.
        </Notice>
      )}
      {model?.note && <Notice>{model.note}</Notice>}
    </>
  );
}
