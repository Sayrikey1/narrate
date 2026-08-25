import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Empty, Loadable, Notice } from "../ui/feedback";
import { Card } from "../ui/layout";
import { DataTable, type Column } from "../ui/Table";
import { DownloadIcon } from "./Icon";
import type { Variant, Voice } from "../types";

/** Every voice this script has been performed in, each exportable on its own.
 *
 *  Nothing is stored to make this work. A take has always recorded the voice
 *  that produced it, so generating a script twice — once in the original voice,
 *  once in a clone — leaves both sets of takes side by side, and a variant is
 *  only a different way of choosing between them. Two masters come out of one
 *  generation history.
 *
 *  A voice that covers fewer chunks than the script has cannot be exported: the
 *  master would have a silent hole where the missing lines are. Saying which
 *  lines are missing is more use than greying out a button.
 */
export function Variants({ scriptId, onExported }: { scriptId: number; onExported?: () => void }) {
  const [variants, setVariants] = useState<Variant[]>([]);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([api.variants(scriptId), api.voices().catch(() => [])])
      .then(([found, allVoices]) => {
        setVariants(found);
        setVoices(allVoices);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [scriptId]);

  useEffect(load, [load]);

  // A voice id is opaque; the name is what a person recognises. Falls back to
  // the id, because a voice deleted from the account still has takes here.
  const nameOf = (id: string) => voices.find((v) => v.voice_id === id)?.name ?? id;

  async function exportVariant(variant: Variant) {
    setBusy(variant.voice_id);
    setError(null);
    setDone(null);
    try {
      const result = await api.export(scriptId, undefined, undefined, {
        voice_id: variant.voice_id,
        label: nameOf(variant.voice_id),
      });
      setDone(`${nameOf(variant.voice_id)} → ${result.out_dir.split("/").slice(-1)[0]}`);
      onExported?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  const columns: readonly Column<Variant>[] = [
    {
      header: "Voice",
      cell: (v) => (
        <>
          <strong>{nameOf(v.voice_id)}</strong>{" "}
          <span className="dim mono">{v.voice_id.slice(0, 12)}</span>
        </>
      ),
    },
    {
      header: "Coverage",
      align: "num",
      cell: (v) => (
        <span className="mono">
          {v.chunks}/{v.chunks_total}
        </span>
      ),
    },
    {
      header: "",
      cell: (v) =>
        v.complete ? (
          <Badge tone="ok">complete</Badge>
        ) : (
          <Badge tone="warn">{v.chunks_total - v.chunks} missing</Badge>
        ),
    },
    {
      header: "",
      align: "num",
      cell: (v) =>
        v.complete ? (
          <button
            className="small"
            disabled={busy !== null}
            onClick={() => void exportVariant(v)}
            title={`Stitch ${nameOf(v.voice_id)}'s performance into its own master`}
          >
            <DownloadIcon /> {busy === v.voice_id ? "Exporting…" : "Export"}
          </button>
        ) : (
          <span className="faint">—</span>
        ),
    },
  ];

  return (
    <Card title="Versions">
      <Loadable loading={loading} error={error} rows={2} onRetry={load}>
        <DataTable
          compact
          columns={columns}
          rows={variants}
          rowKey={(v) => v.voice_id}
          caption="Voices this script has been generated in"
          empty={{
            title: "Nothing generated yet",
            body: "Generate the script and the voice it was made in appears here.",
          }}
        />
        {variants.length > 1 ? (
          <Notice>
            Each of these exports to its own folder, so neither overwrites the
            other. Nothing extra was stored to make this possible — a take
            already records the voice that made it.
          </Notice>
        ) : (
          variants.length === 1 && (
            <Notice>
              To get a second version, change the project's voice and generate
              again with <strong>force</strong>. The existing takes are kept, and
              both versions become exportable from here.
            </Notice>
          )
        )}
        {variants.some((v) => !v.complete) && (
          <Notice tone="warn">
            A voice missing some chunks cannot be exported — the master would have
            a silent gap. Generate the missing lines in that voice first.
          </Notice>
        )}
        {done && <Notice tone="ok">Exported {done}</Notice>}
      </Loadable>
    </Card>
  );
}

/** Standalone empty state for a script with nothing generated. */
export function NoVariants() {
  return (
    <Card title="Versions">
      <Empty title="Nothing generated yet">
        Generate the script, and every voice you generate it in becomes a version
        you can export separately.
      </Empty>
    </Card>
  );
}
