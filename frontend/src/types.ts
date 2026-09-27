export interface Model {
  model_id: string;
  label: string;
  usd_per_1k: number;
  max_chars: number;
  request_stitching: boolean;
  continuity_mode: "request_ids" | "text" | "none";
  audio_tags: boolean;
  /** Which voice settings this model actually honours. On v3 speed,
   *  similarity and speaker boost do nothing, so their controls are hidden
   *  rather than shown doing nothing. */
  settings_honoured: string[];
  /** Whether this model can render several speakers in one request. */
  dialogue: boolean;
  /** The dialogue endpoint's own ceiling, tighter than `max_chars`. */
  dialogue_max_chars: number;
  long_form: string;
  /** What a new project gets when none is chosen — the server's choice. */
  default: boolean;
  note: string;
}

export interface Voice {
  voice_id: string;
  name: string;
  category: string | null;
  labels: Record<string, string>;
  description: string | null;
  /** Whether an audition is possible. The audio itself comes from
   *  `api.voicePreview(voice_id)`, never from the provider's own link. */
  preview: boolean;
  settings: VoiceSettings | null;
  mock: boolean;
}

export interface VoiceSettings {
  stability?: number;
  similarity_boost?: number;
  style?: number;
  speed?: number;
  use_speaker_boost?: boolean;
}

export interface Project {
  id: number;
  name: string;
  voice_id: string | null;
  model_id: string;
  spend_micros: number;
  waste_pct: number;
  cap_micros: number | null;
  cap_used_pct: number;
}

export interface ProjectDetail {
  id: number;
  name: string;
  voice_id: string | null;
  model_id: string;
  prefix_tags: string;
  scripts: { id: number; title: string }[];
  cost: Cost;
  cap_micros: number | null;
  cap_used_pct: number;
}

/** One class of operation — narration, effects, suggestions, probes. */
export interface KindTotal {
  kind: string;
  provider: string;
  unit_kind: "characters" | "seconds" | "tokens";
  operations: number;
  units: number;
  units_display: string;
  cost_micros: number;
  credits: number;
}

export interface MediaFile {
  name: string;
  path: string;
  kind: "export" | "effect" | "take";
  bytes: number;
  label: string;
  duration_s: number | null;
  playable: boolean;
  take_id: number | null;
  effect_id: number | null;
  script_id: number | null;
}

export interface MediaGroup {
  title: string;
  kind: "export" | "effect" | "take";
  bytes: number;
  files: MediaFile[];
}

export interface ProjectMedia {
  project_id: number;
  project: string;
  files: number;
  bytes: number;
  groups: MediaGroup[];
}

export interface ScriptSummary {
  id: number;
  project_id: number;
  title: string;
  chunks: number;
  chars: number;
  /** The script's own model if it has one, else its project's. */
  model_id: string | null;
}

export interface Take {
  id: number;
  ordinal: number;
  status: string;
  billed_chars: number;
  cost_micros: number;
  duration_s: number | null;
  model_id: string;
  in_cut: boolean;
  /** What `narrate verify` found. "clear" means no issues were found — which
   *  is not the same as perfect, and is never drawn as a tick. */
  verify_status: VerifyStatus;
  findings: Finding[];
}

export type VerifyStatus = "unverified" | "clear" | "review" | "suspect" | "error";

/** One difference between the script and what was heard. Times are seconds
 *  into the take. */
export interface Finding {
  severity: "fail" | "review" | "info";
  kind: string;
  summary: string;
  expected?: string;
  heard?: string;
  start_s: number;
  end_s?: number;
  probability?: number;
  gap_s?: number;
  context?: string;
  note?: string;
}

export interface VerifyCapability {
  available: boolean;
  model_ready: boolean;
  model: string;
  model_mb: number;
  install_hint: string;
  download_hint: string;
}

export interface RegeneratePlan {
  ordinal: number;
  chunk_id: number;
  chars: number;
  price_micros: number;
  model_id: string;
  cut_take: number | null;
  cut_status: string;
  blocker: string | null;
}

export type CutMove = "better" | "new" | "never";

export interface RegenerateBody {
  chunks: number[];
  text?: string;
  attempts?: number;
  move?: CutMove;
  max_spend_usd?: number;
  accept_unknown?: boolean;
  /** Rebuild the episode afterwards, if the cut changed or the export is stale. */
  export?: boolean;
  /** The masters to rebuild — the formats chosen on the page. */
  export_formats?: string[];
  confirm?: boolean;
}

/** The price of a regeneration, and — once confirmed — the run to follow. */
export interface RegenerateQuote {
  dry_run: boolean;
  run_key?: string;
  plans: RegeneratePlan[];
  attempts: number;
  worst_micros: number;
  worst_usd: string;
  checked_with: string;
  mock: boolean;
}

export interface ChunkRow {
  ordinal: number;
  text: string;
  chars: number;
  source: string;
  target_start_s: number | null;
  takes: Take[];
}

/** One row of the running order. Effects are overlays, not inserts. */
export interface TimelineEntry {
  index: number;
  kind: "narration" | "effect" | "planned";
  start: string;
  end: string;
  start_s: number;
  end_s: number;
  duration_s: number;
  label: string;
  chunk_ordinal: number | null;
  take_id: number | null;
  slot_id: number | null;
  effect_id: number | null;
  generated: boolean;
  target_s: number | null;
  drift_s: number | null;
}

export interface Timeline {
  script_id: number;
  title: string;
  project: string;
  runtime_s: number;
  complete: boolean;
  entries: TimelineEntry[];
}

/** The editing plan: the document a person reads, and the same facts as data.
 *  Both come from the one endpoint so the page never re-derives from prose. */
export interface Plan {
  markdown: string;
  data: PlanData;
}

export interface PlanData {
  script_id: number;
  title: string;
  project: string;
  runtime_s: number;
  gap_seconds: number;
  cost_micros: number;
  complete: boolean;
  entries: PlanEntry[];
}

export interface PlanEntry {
  index: number;
  kind: "narration" | "effect" | "planned";
  start_s: number;
  end_s: number;
  start: string;
  end: string;
  label: string;
  file: string | null;
  chunk_ordinal: number | null;
  take_id: number | null;
  slot_id: number | null;
  effect_id: number | null;
  generated: boolean;
  target_s: number | null;
  drift_s: number | null;
}

/** A named speaker in a project, and the voice that plays them.
 *
 *  This is what makes `Morag:` in a script a speaker change rather than
 *  ordinary prose — an uncast name is left as narration. */
export interface CastMember {
  id: number;
  name: string;
  voice_id: string;
  note: string;
}

/** A starter script: annotated in HTML comments, which are stripped before
 *  anything is sent — so it explains itself and still generates correctly with
 *  every comment left in place. */
export interface ScriptTemplate {
  slug: string;
  title: string;
  summary: string;
  filename: string;
}

/** One voice's performance of a script, assembled from the takes on record.
 *
 *  Nothing is stored for a variant — a take already records the voice that made
 *  it, so this is a count rather than a new entity. Only a voice covering every
 *  chunk can be exported; a partial one would ship an episode with a hole. */
export interface Variant {
  voice_id: string;
  chunks: number;
  chunks_total: number;
  complete: boolean;
}

export interface Slot {
  id: number;
  at_chunk_ordinal: number;
  description: string;
  duration_s: number | null;
  loop: boolean;
  source: "marker" | "suggested" | "manual";
  accepted: boolean;
  note: string;
  effect_id: number | null;
  slug: string | null;
  cost_micros: number;
}

export interface Cost {
  operations: number;
  takes: number;
  billed_chars: number;
  cost_micros: number;
  credits: number;
  selected_micros: number;
  wasted_micros: number;
  waste_pct: number;
  display: string;
  by_kind: KindTotal[];
  cost_per_minute_micros?: number | null;
  cap_micros?: number | null;
  cap_used_pct?: number;
  outstanding?: {
    chunks: number;
    chars: number;
    effects: number;
    effect_seconds: number;
    cost_micros: number;
  };
  projected_micros?: number;
}

export interface ExportResult {
  out_dir: string;
  master: string;
  masters: Record<string, string>;
  /** The same masters with the effects mixed in — `<title>_fx.*`. */
  fx_masters: Record<string, string>;
  effects_mixed: number;
  mp3: string | null;
  plan: string;
  duration_s: number;
  chunks: number;
  effects: number;
  planned: number;
  names: Record<string, string>;
}


export interface ScriptCreated {
  id: number;
  chunks: number;
  slots: number;
  warnings: string[];
  /** Who the parser found speaking, in first-appearance order. */
  speakers?: string[];
  turns_assigned?: number;
  /** True when turns were packed as conversations rather than one per chunk. */
  dialogue?: boolean;
}

export interface AudioFormatInfo {
  key: string;
  label: string;
  suffix: string;
  codec: string;
  lossless: boolean;
  note: string;
}


/** What a started run reports before any audio exists: the combined estimate
 *  for both phases, so the UI can show the split it is about to spend. */
export interface GenerateStarted {
  run_key: string;
  streaming: boolean;
  projection: {
    speech_micros: number;
    effect_micros: number;
    total_micros: number;
    chunks: number;
    chars: number;
    effects: number;
    effect_seconds: number;
  };
}

/** The publish pack: the upload side of what `plan.md` is for the edit. */
export interface Publish {
  markdown: string;
  data: PublishData;
}

export interface PublishData {
  script_id: number;
  script_title: string;
  project: string;
  runtime_s: number;
  complete: boolean;
  title: PublishTitle;
  description: string;
  boilerplate: string;
  tags: string[];
  chapters: PublishChapters;
  thumbnail: ThumbnailBrief | null;
  briefs: BriefSummary[];
  retention: Retention;
}

export interface PublishTitle {
  chosen: string;
  over_length: boolean;
  candidates: TitleCandidate[];
}

export interface TitleCandidate {
  id: number;
  text: string;
  formula: string;
  rationale: string;
  accepted: boolean;
  proposed: boolean;
  over_length: boolean;
}

export interface PublishChapters {
  usable: boolean;
  problems: string[];
  text: string;
  entries: ChapterEntry[];
}

export interface ChapterEntry {
  stamp: string;
  start_s: number;
  title: string;
  chunk_ordinal: number | null;
}

export interface ThumbnailBrief {
  id: number;
  archetype: string;
  overlay_text: string;
  word_count: number;
  subject: string;
  contrast: string;
  rationale: string;
  principles: string;
}

export interface BriefSummary {
  id: number;
  archetype: string;
  overlay_text: string;
  accepted: boolean;
}

export interface Retention {
  good_pct: number;
  great_pct: number;
  good_hold_s: number;
  great_hold_s: number;
  extrapolated: boolean;
}

/** What deleting an episode or a project would do — or, once `done`, did. */
export interface DeletePlan {
  kind: "episode" | "project";
  id: number;
  name: string;
  episodes: number[];
  takes: number;
  /** What it cost — stays on the record and in every total. */
  spend_micros: number;
  spend_usd: string;
  /** Audio and exports that "delete the files too" would remove. */
  files: number;
  file_bytes: number;
  running: number[];
  done: boolean;
  files_deleted: number;
}

export interface DeleteBody {
  confirm?: boolean;
  delete_files?: boolean;
  force?: boolean;
}

/** What replacing an episode's script would do: priced before anything changes. */
export interface ReplacePlan {
  script_id: number;
  title: string;
  unchanged: boolean;
  kept: number;
  kept_takes: number;
  kept_regenerated: number;
  new: number;
  retired: number;
  retired_takes: number;
  retired_to: number | null;
  beats_detached: number;
  /** Hand-placed or suggested effect slots whose passage the new script drops. */
  slots_dropped: number;
  chunks_to_generate: number;
  chars: number;
  effects_to_generate: number;
  quote_micros: number;
  quote_usd: string;
  warnings: string[];
  done: boolean;
}

export interface Deleted {
  projects: {
    id: number;
    name: string;
    spend_micros: number;
    wasted_micros: number;
    deleted_at: string | null;
  }[];
  scripts: {
    id: number;
    title: string;
    project_id: number;
    spend_micros: number;
    deleted_at: string | null;
    /** Deleted along with its project — restored with it, not alone. */
    with_project: boolean;
  }[];
}
