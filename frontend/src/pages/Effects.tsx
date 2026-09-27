import { EffectPanel } from "../components/EffectPanel";
import { Notice } from "../ui/feedback";
import { Card, PageHeader } from "../ui/layout";
import type { ScriptSummary, Slot } from "../types";

export function EffectsPage({
  scriptId,
  script,
  slots,
  onRefresh,
}: {
  scriptId: number;
  script: ScriptSummary | null;
  slots: Slot[];
  onRefresh: () => void;
}) {
  const planned = slots.filter((s) => s.accepted && !s.effect_id).length;

  return (
    <>
      <PageHeader
        title="Effects"
        subtitle={
          <>
            {script?.title}
            {slots.length === 0
              ? " · no cues yet"
              : planned
                ? ` · ${planned} cue${planned === 1 ? "" : "s"} still to generate`
                : " · all cues generated"}
          </>
        }
      />

      <Card>
        <EffectPanel scriptId={scriptId} slots={slots} busy={false} onChange={onRefresh} />
        <Notice>
          A cue is generated once and placed as often as you like — repeats cost
          nothing. The export mixes the effects into an <code>_fx</code> version of
          each master, and keeps the plain master and each effect's own file for an
          editor who wants them on a separate track.
        </Notice>
      </Card>
    </>
  );
}
