import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { initial } from "../components/Rail";
import * as transport from "../transport";
import { DocumentView } from "../ui/DocumentView";
import { Empty, Notice, Stat } from "../ui/feedback";
import { PageHeader } from "../ui/layout";
import { TabPanel, Tabs } from "../ui/Tabs";
import { formatClock, parseStamp } from "../time";
import type { Publish, PublishData, ScriptSummary, TitleCandidate } from "../types";

/** YouTube cuts a title off in most places past this many characters. */
const TITLE_LIMIT = 60;
/** More words than this on a thumbnail stop reading at phone size. */
const OVERLAY_WORDS = 5;

/** The publish pack: what goes in the box the video ships in.
 *
 *  `plan.md` is for cutting the episode; this is for uploading it. **Packaging**
 *  comes first, because it is where the decisions are made — choosing a title,
 *  choosing a thumbnail brief, seeing which chapter rules are broken — and
 *  every fact in it comes from the endpoint's data twin rather than from
 *  re-parsing the markdown beside it. Deriving the same fact twice is how two
 *  answers to one question happen.
 *
 *  Pressing a chapter plays the episode from there, which is the fastest way to
 *  check that a chapter actually lands where its name says it does.
 */
export function PublishPage({
  scriptId,
  script,
}: {
  scriptId: number;
  script: ScriptSummary | null;
}) {
  const [tab, setTab] = useState<"packaging" | "document">("packaging");
  const [pack, setPack] = useState<Publish | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setPack(await api.publish(scriptId));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [scriptId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <>
        <Head script={script} />
        <Notice tone="error" title="Could not load the publish pack">
          {error}
        </Notice>
      </>
    );
  }

  if (!pack) {
    return (
      <>
        <Head script={script} />
        <div className="panel">
          <Empty title="Nothing to publish yet">
            Ingest a script and the pack fills in. Chapters and the retention target need no API
            key at all.
          </Empty>
        </div>
      </>
    );
  }

  return (
    <>
      <Head script={script} pack={pack} />

      <Tabs label="How to read the pack" tabs={TABS} active={tab} onChange={setTab} />

      <TabPanel id={tab}>
        {tab === "packaging" ? (
          <Packaging scriptId={scriptId} data={pack.data} onChange={load} />
        ) : (
          <DocumentView
            source={pack.markdown}
            filename="publish.md"
            onRowActivate={(cells) => seekTo(cells)}
            aside={<Summary data={pack.data} />}
          />
        )}
      </TabPanel>
    </>
  );
}

const TABS = [
  { id: "packaging" as const, label: "Packaging" },
  { id: "document" as const, label: "Document" },
];

function Head({ script, pack }: { script: ScriptSummary | null; pack?: Publish }) {
  const [copied, setCopied] = useState(false);
  const chapters = pack?.data.chapters.text ?? "";
  // A file to save that already exists as text in the pack. Revoked when the
  // text changes, so a long session does not leak one per reload.
  const href = useMemo(
    () => (chapters ? URL.createObjectURL(new Blob([chapters], { type: "text/plain" })) : null),
    [chapters],
  );
  useEffect(() => () => {
    if (href) URL.revokeObjectURL(href);
  }, [href]);

  async function copy() {
    if (!pack) return;
    try {
      await navigator.clipboard.writeText(pack.data.description_box);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard access can be refused outright; the Document tab still has
      // the text to select by hand.
    }
  }

  return (
    <PageHeader
      kicker={`${script?.title ?? "This episode"} · ready for YouTube`}
      title="Publish pack"
      subtitle="Chapters, title, description, tags and the thumbnail brief."
      actions={
        pack && (
          <>
            {href && (
              <a className="btn large" href={href} download="chapters.txt">
                ⤓ Chapters .txt
              </a>
            )}
            <button
              type="button"
              className="primary large"
              disabled={!pack.data.description_box}
              onClick={() => void copy()}
              title="The description, its chapters and the channel's footer, as YouTube's description box wants them"
            >
              {copied ? "✓ Copied" : "📋 Copy the description"}
            </button>
          </>
        )
      }
    />
  );
}

/** A chapter row plays from its stamp, so a name can be checked against the
 *  audio it claims to describe. */
function seekTo(cells: string[]): void {
  for (const cell of cells) {
    const seconds = parseStamp(cell);
    if (seconds === null) continue;
    transport.seek(seconds);
    transport.play();
    return;
  }
}

function Summary({ data }: { data: PublishData }) {
  const { chapters, retention, tags } = data;
  return (
    <>
      <Stat label="Runtime" value={formatClock(data.runtime_s)} />
      <Stat label="Chapters" value={String(chapters.entries.length)} tone={chapters.usable ? "ok" : "warn"} />
      {retention.good_pct > 0 && (
        <>
          <Stat
            label="Target average view"
            value={`${retention.good_pct}%`}
            tone={retention.extrapolated ? "warn" : undefined}
          />
          <Stat label="Hold for" value={formatClock(retention.good_hold_s)} />
        </>
      )}
      <Stat label="Tags" value={String(tags.length)} />
    </>
  );
}

function Packaging({
  scriptId,
  data,
  onChange,
}: {
  scriptId: number;
  data: PublishData;
  onChange: () => void;
}) {
  return (
    <div className="row">
      <div className="grow">
        <FeedPreview data={data} />
        <Chapters data={data} />
        <Titles scriptId={scriptId} data={data} onChange={onChange} />
      </div>
      <div className="side">
        <Thumbnail scriptId={scriptId} data={data} onChange={onChange} />
        {data.retention.good_pct > 0 && <RetentionTarget data={data} />}
        <DescriptionAndTags data={data} />
      </div>
    </div>
  );
}

/** How the video will sit in a feed: a sketch of the thumbnail from its brief,
 *  the chosen title under it, and the checks a feed is unforgiving about. */
function FeedPreview({ data }: { data: PublishData }) {
  const brief = data.thumbnail;
  const title = data.title.chosen || data.script_title;
  const chars = data.title.chosen.length;
  const over = data.title.over_length || chars > TITLE_LIMIT;
  const shared = brief ? sharedWords(title, brief.overlay_text) : [];
  const lines = brief ? overlayLines(brief.overlay_text) : [];
  const ends = [...data.chapters.entries.map((c) => c.start_s).slice(1), data.runtime_s];

  return (
    <section className="panel" aria-labelledby="feed-h">
      <div className="card-head">
        <h2 id="feed-h" className="card-title">
          👀 How it reads in a feed
        </h2>
        <span className="faint small-print">A sketch from the brief — no image is generated</span>
      </div>
      <div className="feed">
        <div className="feed-main">
          <div
            className="thumb"
            role="img"
            aria-label={
              brief
                ? `Thumbnail sketch: ${brief.archetype}; the words ${brief.overlay_text}; ${brief.contrast}`
                : "No thumbnail brief yet"
            }
          >
            {brief ? (
              <>
                <div className="thumb-subject">📷 {brief.subject}</div>
                <div className="thumb-words">
                  {lines.map((line, i) => (
                    <span key={i} className={i === 1 || (lines.length === 1 && i === 0) ? "thumb-line hot" : "thumb-line"}>
                      {line}
                    </span>
                  ))}
                </div>
                <span className="thumb-tag">BRIEF · sketch</span>
              </>
            ) : (
              <span className="thumb-empty">No thumbnail brief yet</span>
            )}
            <span className="thumb-time mono">{formatClock(data.runtime_s)}</span>
          </div>
          {data.chapters.entries.length > 1 && (
            <div className="chapter-bar" aria-hidden>
              {data.chapters.entries.map((c, i) => (
                <span key={c.stamp} style={{ flexGrow: Math.max(1, (ends[i] ?? c.start_s) - c.start_s) }} />
              ))}
            </div>
          )}
          <div className="feed-meta">
            <span className="icon-tile sz-38 round" aria-hidden>
              {initial(data.project)}
            </span>
            <div>
              <strong>{title}</strong>
              <span>{data.project} · [views] · [posted]</span>
            </div>
          </div>
        </div>
        <div className="feed-checks">
          <div className="tile">
            <span className="tile-label">Title</span>
            <span className={over ? "tile-value warn" : chars ? "tile-value ok" : "tile-value"}>
              {chars} / {TITLE_LIMIT}
            </span>
            <span className="tile-note">
              {!chars
                ? "No title chosen yet"
                : over
                  ? "Cut off in most places YouTube shows it"
                  : "Fits before YouTube cuts it off"}
            </span>
          </div>
          {brief && (
            <div className="tile">
              <span className="tile-label">Text on image</span>
              <span className={brief.word_count <= OVERLAY_WORDS ? "tile-value ok" : "tile-value warn"}>
                {brief.word_count} word{brief.word_count === 1 ? "" : "s"}
              </span>
              <span className="tile-note">
                {brief.word_count <= OVERLAY_WORDS
                  ? "Five or fewer reads at phone size"
                  : "More than five is hard to read at phone size"}
              </span>
            </div>
          )}
          {brief && chars > 0 && (
            <div className="tile">
              <span className="tile-label">Title and image</span>
              <span className={shared.length ? "tile-verdict warn" : "tile-verdict ok"}>
                {shared.length ? `Both say “${shared[0]}”` : "Don't repeat each other ✓"}
              </span>
              <span className="tile-note">
                {shared.length
                  ? "The image is a second line, not a copy of the first"
                  : brief.principles || "Each says something the other doesn't"}
              </span>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function Chapters({ data }: { data: PublishData }) {
  const { chapters } = data;
  const entries = chapters.entries;
  const gaps = entries.slice(1).map((c, i) => c.start_s - entries[i]!.start_s);
  const rules: [boolean, string][] = entries.length
    ? [
        [entries[0]!.start_s === 0, entries[0]!.start_s === 0 ? "First chapter at 0:00" : `First chapter at ${entries[0]!.stamp}, not 0:00`],
        [entries.length >= 3, `${entries.length} chapter${entries.length === 1 ? "" : "s"} — needs at least 3`],
        [gaps.every((g) => g >= 10), "Each at least 10 s apart"],
      ]
    : [];

  return (
    <section className="panel" aria-labelledby="chapters-h">
      <div className="card-head">
        <h2 id="chapters-h" className="card-title">
          📑 Chapters
        </h2>
        {entries.length > 0 &&
          (chapters.usable ? (
            <span className="pill ok">✅ YouTube will accept these</span>
          ) : (
            <span className="pill bad">⚠️ YouTube would not accept these</span>
          ))}
      </div>
      {rules.length > 0 && (
        <div className="rules">
          {rules.map(([ok, text]) => (
            <span key={text} className={ok ? "rule" : "rule broken"}>
              {ok ? "✓" : "✗"} {text}
            </span>
          ))}
        </div>
      )}
      {/* With none named, the empty state below says so — once. */}
      {entries.length > 0 &&
        chapters.problems.map((problem) => (
          <Notice key={problem} tone="warn" title="YouTube would not accept this">
            {problem}
          </Notice>
        ))}
      {entries.length > 0 ? (
        <div className="chapter-list">
          {entries.map((c) => (
            <button
              key={`${c.stamp}-${c.title}`}
              type="button"
              className="chapter-row"
              aria-label={`Play chapter ${c.title} from ${c.stamp}`}
              onClick={() => {
                transport.seek(c.start_s);
                transport.play();
              }}
            >
              <span className="chapter-play" aria-hidden>
                ▶
              </span>
              <span className="mono chapter-at">{c.stamp}</span>
              <span className="chapter-title">{c.title}</span>
              {c.chunk_ordinal !== null && <span className="faint">chunk {c.chunk_ordinal}</span>}
            </button>
          ))}
        </div>
      ) : (
        <Empty title="No chapters named">
          Add a <code>## Heading</code>, a <code>[CHAPTER: ...]</code> marker, or a name on an
          anchor — <code>[@ 03:00 The Employee Trap]</code> — then re-chunk.
        </Empty>
      )}
      {entries.length > 0 && (
        <p className="note flush">
          Times are measured from the exported audio, not the script's anchors. Press a chapter to
          hear it. Rename one with <code>## Heading</code> in the script.
        </p>
      )}
    </section>
  );
}

function Titles({
  scriptId,
  data,
  onChange,
}: {
  scriptId: number;
  data: PublishData;
  onChange: () => void;
}) {
  const { title } = data;
  return (
    <section className="panel" aria-labelledby="titles-h">
      <div className="card-head">
        <h2 id="titles-h" className="card-title">
          🏷️ Titles
        </h2>
      </div>
      {title.candidates.length > 0 ? (
        <div className="title-list">
          {title.candidates.map((candidate) => (
            <TitleOption key={candidate.id} scriptId={scriptId} candidate={candidate} onChange={onChange} />
          ))}
        </div>
      ) : title.chosen ? (
        <div className="title-option chosen">
          <div className="title-option-text">
            <strong>{title.chosen}</strong>
          </div>
        </div>
      ) : (
        <Empty title="No title chosen">
          Generate candidates with <code>narrate publish draft --go</code>.
        </Empty>
      )}
    </section>
  );
}

function TitleOption({
  scriptId,
  candidate,
  onChange,
}: {
  scriptId: number;
  candidate: TitleCandidate;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const chars = candidate.text.length;
  return (
    <div className={candidate.accepted ? "title-option chosen" : "title-option"}>
      <div className="title-option-text">
        <div className="title-option-tags">
          {candidate.formula && <span className="pill plain small-pill">{candidate.formula}</span>}
          <span className={candidate.over_length ? "pill warn small-pill mono" : "pill ok small-pill mono"}>
            {chars} chars{candidate.over_length ? " · cut off" : ""}
          </span>
        </div>
        <strong>{candidate.text}</strong>
        {candidate.rationale && <span className="dim">{candidate.rationale}</span>}
      </div>
      <button
        type="button"
        className={candidate.accepted ? "small primary" : "small"}
        aria-pressed={candidate.accepted}
        disabled={busy || candidate.accepted}
        onClick={async () => {
          setBusy(true);
          try {
            await api.acceptTitle(scriptId, candidate.id);
            onChange();
          } finally {
            setBusy(false);
          }
        }}
      >
        {candidate.accepted ? "✓ Chosen" : busy ? "Choosing…" : "Choose"}
      </button>
    </div>
  );
}

function Thumbnail({
  scriptId,
  data,
  onChange,
}: {
  scriptId: number;
  data: PublishData;
  onChange: () => void;
}) {
  const brief = data.thumbnail;
  const others = data.briefs.filter((b) => !b.accepted && b.id !== brief?.id);
  const [busy, setBusy] = useState<number | null>(null);

  return (
    <section className="panel" aria-labelledby="thumb-h">
      <div className="card-head">
        <h2 id="thumb-h" className="card-title">
          🖼️ Thumbnail brief
        </h2>
        {brief && <span className="pill accent-soft">chosen</span>}
      </div>
      {brief ? (
        <dl className="brief">
          <div>
            <dt>Composition</dt>
            <dd>{brief.archetype}</dd>
          </div>
          <div>
            <dt>Text on image</dt>
            <dd className="brief-words">
              <strong>{brief.overlay_text}</strong>
              <span className={brief.word_count <= OVERLAY_WORDS ? "pill ok small-pill" : "pill warn small-pill"}>
                {brief.word_count} words{brief.word_count <= OVERLAY_WORDS ? " ✓" : ""}
              </span>
            </dd>
          </div>
          <div>
            <dt>Subject</dt>
            <dd>{brief.subject}</dd>
          </div>
          <div>
            <dt>Contrast</dt>
            <dd>{brief.contrast}</dd>
          </div>
          {brief.principles && (
            <div>
              <dt>Leans on</dt>
              <dd>{brief.principles}</dd>
            </div>
          )}
        </dl>
      ) : (
        <Empty title="No brief yet">
          <code>narrate publish brief --go</code>. No image is generated — the brief is the
          deliverable.
        </Empty>
      )}
      {others.length > 0 && (
        <div className="other-briefs">
          <span className="faint">Other briefs</span>
          {others.map((other) => (
            <div key={other.id} className="other-brief">
              <span>
                {other.archetype} — <strong>{other.overlay_text}</strong>
              </span>
              <button
                type="button"
                className="small"
                disabled={busy !== null}
                onClick={async () => {
                  setBusy(other.id);
                  try {
                    await api.chooseBrief(scriptId, other.id);
                    onChange();
                  } finally {
                    setBusy(null);
                  }
                }}
              >
                {busy === other.id ? "Choosing…" : "Choose"}
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function RetentionTarget({ data }: { data: PublishData }) {
  const { retention, runtime_s } = data;
  return (
    <section className="panel" aria-labelledby="ret-h">
      <div className="card-head">
        <h2 id="ret-h" className="card-title">
          📈 Retention target
        </h2>
      </div>
      <div className="tiles retention-tiles">
        <div className="tile">
          <span className="dim">👍 Doing well</span>
          <span className="retention-figure">{retention.good_pct}%</span>
          <span className="mono faint">{formatClock(retention.good_hold_s)} watched</span>
        </div>
        <div className="tile accent">
          <span className="dim">🔥 Doing very well</span>
          <span className="retention-figure">{retention.great_pct}%</span>
          <span className="mono faint">{formatClock(retention.great_hold_s)} watched</span>
        </div>
      </div>
      <div className="retention-bar" aria-hidden>
        <span className="retention-good" style={{ width: `${retention.good_pct}%` }} />
        <span
          className="retention-great"
          style={{ left: `${retention.good_pct}%`, width: `${Math.max(0, retention.great_pct - retention.good_pct)}%` }}
        />
      </div>
      <p className="note flush">
        A {formatClock(runtime_s)} episode should hold <strong>{retention.good_pct}%</strong> of
        viewers to be doing well, and <strong>{retention.great_pct}%</strong> to be doing very
        well.
      </p>
      {retention.extrapolated && (
        <Notice tone="warn" title="Extrapolated">
          Outside the published 8 to 120 minute range, so these are extrapolated rather than
          quoted.
        </Notice>
      )}
    </section>
  );
}

/** What the description and tags are. Editing them here comes later; for now
 *  they are set from the command line, and the copy button takes them as-is. */
function DescriptionAndTags({ data }: { data: PublishData }) {
  const chapters = data.chapters.usable ? data.chapters.entries.length : 0;
  const added = [
    chapters ? `the ${chapters} chapters` : null,
    data.boilerplate ? "your channel's footer" : null,
  ].filter(Boolean) as string[];
  const sentence = added.join(" and ");
  return (
    <section className="panel" aria-labelledby="desc-h">
      <div className="card-head">
        <h2 id="desc-h" className="card-title">
          📝 Description &amp; tags
        </h2>
      </div>
      {data.description ? (
        <p className="description">{data.description}</p>
      ) : (
        <p className="note flush">
          Nothing written yet. Set it with{" "}
          <code>narrate publish set {data.script_id} --description</code>.
        </p>
      )}
      {added.length > 0 && (
        <p className="field-hint flush-hint">
          {sentence.charAt(0).toUpperCase() + sentence.slice(1)} {chapters ? "are" : "is"} added
          below it when you copy the description.
        </p>
      )}
      {data.tags.length > 0 ? (
        <div className="tag-list" role="list" aria-label="Tags">
          {data.tags.map((tag) => (
            <span key={tag} role="listitem" className="pill plain">
              {tag}
            </span>
          ))}
        </div>
      ) : (
        <p className="note flush">
          No tags yet. <code>narrate publish set {data.script_id} --tags a,b,c</code>
        </p>
      )}
    </section>
  );
}

/** Words of four letters or more that a title and a thumbnail both say. */
function sharedWords(title: string, overlay: string): string[] {
  const words = (text: string) =>
    new Set(text.toLowerCase().match(/[a-z0-9$£€%]{4,}/g) ?? []);
  const inTitle = words(title);
  return [...words(overlay)].filter((w) => inTitle.has(w));
}

/** The overlay as a thumbnail sets it: two or three short lines. */
function overlayLines(text: string): string[] {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length <= 2) return words.length ? [words.join(" ")] : [];
  if (words.length === 3) return words;
  const size = Math.ceil(words.length / 3);
  return [words.slice(0, size), words.slice(size, size * 2), words.slice(size * 2)]
    .map((line) => line.join(" "))
    .filter(Boolean);
}
