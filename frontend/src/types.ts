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
