import type {
  AudioFormatInfo,
  CastMember,
  ChunkRow,
  Cost,
  ExportResult,
  GenerateStarted,
  Model,
  Plan,
  Project,
  ProjectDetail,
  ProjectMedia,
  Publish,
  ScriptCreated,
  ScriptSummary,
  ScriptTemplate,
  Slot,
  Timeline,
  Variant,
  Voice,
  VoiceSettings,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    // FastAPI puts the useful message in `detail`; surfacing it beats a bare
    // status code, especially for the 409s that carry a "do this instead".
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

const patch = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });

export const api = {
  models: () => request<Model[]>("/api/models"),
  voices: (search?: string) =>
    request<Voice[]>(`/api/voices${search ? `?search=${encodeURIComponent(search)}` : ""}`),

  projects: () => request<Project[]>("/api/projects"),
  project: (id: number) => request<ProjectDetail>(`/api/projects/${id}`),
  createProject: (body: {
    name: string;
    voice_id?: string;
    model_id?: string;
    prefix_tags?: string;
    monthly_cap_usd?: number;
  }) => post<{ id: number }>("/api/projects", body),
  updateProject: (
    id: number,
    body: {
      voice_id?: string;
      model_id?: string;
      prefix_tags?: string;
      settings?: VoiceSettings;
      monthly_cap_usd?: number;
    },
  ) =>
    patch<{ id: number; settings: VoiceSettings; rejected_settings: string[] }>(
      `/api/projects/${id}`,
      body,
    ),

  media: (projectId: number) => request<ProjectMedia>(`/api/projects/${projectId}/media`),

  cast: (projectId: number) => request<CastMember[]>(`/api/projects/${projectId}/cast`),
  setCastMember: (projectId: number, body: { name: string; voice_id: string; note?: string }) =>
    post<CastMember>(`/api/projects/${projectId}/cast`, body),
  removeCastMember: (projectId: number, memberId: number) =>
    request(`/api/projects/${projectId}/cast/${memberId}`, { method: "DELETE" }),

  scripts: () => request<ScriptSummary[]>("/api/scripts"),
  createScript: (body: {
    project_id: number;
    title: string;
    text: string;
    dialogue?: boolean;
  }) => post<ScriptCreated>("/api/scripts", body),

  /** Multipart, because the server validates the bytes rather than trusting
   *  the extension. Content-Type is left to the browser so the boundary is
   *  generated correctly. */
  uploadScript: async (projectId: number, file: File, title?: string, dialogue = false) => {
    const form = new FormData();
    form.append("project_id", String(projectId));
    form.append("file", file);
    if (title) form.append("title", title);
    if (dialogue) form.append("dialogue", "true");
    const response = await fetch("/api/scripts/upload", { method: "POST", body: form });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        if (body?.detail) detail = body.detail;
      } catch {
        /* non-JSON error body */
      }
      throw new Error(detail);
    }
    return (await response.json()) as ScriptCreated;
  },
  scriptDownloadUrl: (id: number, format: "md" | "txt" = "md") =>
    `/api/scripts/${id}/download?format=${format}`,

  chunks: (id: number) => request<ChunkRow[]>(`/api/scripts/${id}/chunks`),
  timeline: (id: number) => request<Timeline>(`/api/scripts/${id}/timeline`),
  plan: (id: number) => request<Plan>(`/api/scripts/${id}/plan`),
  publish: (id: number) => request<Publish>(`/api/scripts/${id}/publish`),
  acceptTitle: (scriptId: number, candidateId: number) =>
    request<{ id: number; text: string }>(
      `/api/scripts/${scriptId}/titles/${candidateId}/accept`,
      { method: "POST" },
    ),
  chooseBrief: (scriptId: number, briefId: number) =>
    request<{ id: number; archetype: string }>(
      `/api/scripts/${scriptId}/briefs/${briefId}/choose`,
      { method: "POST" },
    ),
  cost: (id: number) => request<Cost>(`/api/scripts/${id}/cost`),
  variants: (id: number) => request<Variant[]>(`/api/scripts/${id}/variants`),

  generate: (
    id: number,
    body: { dry_run: boolean; only?: number[]; force?: boolean; with_effects?: boolean },
  ) => post<GenerateStarted>(`/api/scripts/${id}/generate`, body),
  setCut: (id: number, chunk_ordinal: number, take_ordinal: number) =>
    post(`/api/scripts/${id}/cut`, { chunk_ordinal, take_ordinal }),

  slots: (id: number) => request<Slot[]>(`/api/scripts/${id}/effects`),
  addSlot: (id: number, body: Omit<Slot, "id" | "source" | "accepted" | "note" | "effect_id" | "slug" | "cost_micros">) =>
    post<{ id: number }>(`/api/scripts/${id}/effects`, body),
  acceptSlot: (slotId: number, accepted: boolean) =>
    post(`/api/effects/slots/${slotId}/accept?accepted=${accepted}`),
  deleteSlot: (slotId: number) =>
    request(`/api/effects/slots/${slotId}`, { method: "DELETE" }),
  generateEffects: (id: number, dry_run: boolean) =>
    post<{ outcomes: { status: string; description: string }[]; cost_micros: number }>(
      `/api/scripts/${id}/effects/generate`,
      { dry_run },
    ),

  templates: () => request<ScriptTemplate[]>("/api/templates"),
  /** `download` makes the browser save it rather than show it. */
  templateUrl: (slug: string, download = false) =>
    `/api/templates/${encodeURIComponent(slug)}${download ? "?download=1" : ""}`,

  formats: () => request<AudioFormatInfo[]>("/api/formats"),
  export: (
    id: number,
    formats?: string[],
    pieceFormat?: string,
    /** Export one voice's performance instead of the cut. */
    variant?: { voice_id: string; label?: string },
  ) =>
    post<ExportResult>(`/api/scripts/${id}/export`, {
      formats: formats ?? null,
      piece_format: pieceFormat ?? null,
      voice_id: variant?.voice_id ?? null,
      variant_label: variant?.label ?? null,
    }),

  audioTake: (takeId: number) => `/api/audio/take/${takeId}`,
  audioEffect: (effectId: number) => `/api/audio/effect/${effectId}`,

  /** A voice sample, proxied and cached by our own server rather than fetched
   *  from the provider's CDN — the direct link is nullable, expiring and
   *  cross-origin, and every one of those failures looks like a dead button. */
  voicePreview: (voiceId: string) =>
    `/api/voices/${encodeURIComponent(voiceId)}/preview`,

  /** A text artifact's contents, so a document can be read before committing
   *  to a download. Plain text rather than JSON — this is the file itself. */
  mediaText: async (path: string) => {
    const response = await fetch(`/api/media/file?path=${encodeURIComponent(path)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.text();
  },

  /** Same file either way; the flag chooses inline playback or a download. */
  mediaUrl: (path: string, download = false) =>
    `/api/media/file?path=${encodeURIComponent(path)}${download ? "&download=1" : ""}`,
  archiveUrl: (scriptId: number) => `/api/exports/${scriptId}/archive`,

  streamRun,
};

/** Bytes as something a person reads. */
export function bytes(size: number): string {
  if (size < 1024) return `${size} B`;
  const units = ["KB", "MB", "GB"];
  let value = size / 1024;
  for (const unit of units) {
    if (value < 1024 || unit === "GB") return `${value.toFixed(1)} ${unit}`;
    value /= 1024;
  }
  return `${value.toFixed(1)} GB`;
}

/** Micro-USD to a readable amount. Sub-cent figures are the norm here, so
 *  four decimal places by default — rounding a re-roll to $0.01 would hide
 *  exactly the spend the ledger exists to expose. */
export function usd(micros: number, places = 4): string {
  return `$${(micros / 1_000_000).toFixed(places)}`;
}

/** Subscribe to a run's progress. Returns an unsubscribe function. */
export function streamRun(
  runKey: string,
  onMessage: (message: string) => void,
  onDone: (result: Record<string, unknown>) => void,
): () => void {
  const source = new EventSource(`/api/runs/${runKey}/events`);
  source.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.message) onMessage(payload.message);
  };
  source.addEventListener("done", (event) => {
    onDone(JSON.parse((event as MessageEvent).data));
    source.close();
  });
  source.onerror = () => source.close();
  return () => source.close();
}
