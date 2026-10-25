import { useEffect, useId, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Audition } from "./Audition";
import type { Model, Voice, VoiceSettings } from "../types";

/** Slider ranges. `stability` and `style` are 0–1; `speed` is clamped by the
 *  provider to 0.7–1.2 and going outside that is documented to hurt quality.
 *  The ends say what moving that way does, since "0.35" tells nobody that. */
const RANGES: Record<
  string,
  { min: number; max: number; step: number; label: string; ends: [string, string] }
> = {
  stability: { min: 0, max: 1, step: 0.05, label: "Stability", ends: ["More expressive", "Steadier"] },
  similarity_boost: {
    min: 0,
    max: 1,
    step: 0.05,
    label: "Similarity",
    ends: ["Looser", "Closer to the voice"],
  },
  style: { min: 0, max: 1, step: 0.05, label: "Style", ends: ["Neutral", "Exaggerated"] },
  speed: { min: 0.7, max: 1.2, step: 0.05, label: "Speed", ends: ["Slower", "Faster"] },
};

/** Choose a voice and its delivery before anything is generated.
 *
 *  The two halves are separate components so a form can lay them out apart —
 *  the project settings put the voice beside the model and the delivery beside
 *  the cap — and this composes them for anywhere that wants both together.
 */
export function VoicePicker({
  model,
  voiceId,
  settings,
  onVoice,
  onSettings,
  disabled,
}: {
  model: Model | undefined;
  voiceId: string | null;
  settings: VoiceSettings;
  onVoice: (id: string) => void;
  onSettings: (next: VoiceSettings) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <VoiceList voiceId={voiceId} onVoice={onVoice} disabled={disabled} />
      <Delivery model={model} settings={settings} onSettings={onSettings} disabled={disabled} />
    </div>
  );
}

/** The voices, filterable, each with a sample to hear.
 *
 *  **Auditioning and selecting are separate acts.** They were not: each row
 *  used to be a `<label>` wrapping both a radio and a play button. `<button>`
 *  is a labelable element, so that label had two labelable descendants — invalid
 *  HTML, and browsers disagree about what a click inside one means. The click
 *  was retargeted to the radio, so pressing play also selected the voice and
 *  often stopped the audio it had just started. Hearing a voice before
 *  committing to it was impossible.
 *
 *  The row is now a `role="radio"` button with the audition as its *sibling*.
 *  Nothing interactive is nested inside anything else interactive, which is
 *  both the fix and the invariant the tests hold. */
export function VoiceList({
  voiceId,
  onVoice,
  disabled,
  grid,
}: {
  voiceId: string | null;
  onVoice: (id: string) => void;
  disabled?: boolean;
  /** Two columns or more where there is room — the cast form's layout. */
  grid?: boolean;
}) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const list = useRef<HTMLDivElement | null>(null);
  const heading = useId();

  useEffect(() => {
    setLoading(true);
    api
      .voices()
      .then(setVoices)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return voices;
    return voices.filter(
      (v) =>
        v.name.toLowerCase().includes(needle) ||
        Object.values(v.labels).some((l) => l.toLowerCase().includes(needle)),
    );
  }, [voices, query]);

  const selected = voices.find((v) => v.voice_id === voiceId) ?? null;

  return (
    <div className="field voice-field">
      <div className="voice-head">
        <span id={heading} className="voice-heading">
          Voice
        </span>
        <span className="faint">Hearing a voice doesn't choose it</span>
      </div>
      {loading && <p className="dim">Loading voices…</p>}
      {error && <p className="error">{error}</p>}
      {!loading && !voices.length && (
        <p className="dim">
          No voices available. Add <code>ELEVEN_API</code> to <code>.env</code>.
        </p>
      )}
      {voices.length > 0 && (
        <>
          <label className="search-box">
            <SearchGlyph />
            <input
              type="search"
              aria-label="Filter voices"
              value={query}
              placeholder="Filter by name or accent…"
              onChange={(e) => setQuery(e.target.value)}
              disabled={disabled}
            />
          </label>
          <div
            className={grid ? "voice-list grid" : "voice-list"}
            role="radiogroup"
            aria-labelledby={heading}
            ref={list}
          >
            {filtered.map((voice, index) => {
              const chosen = voice.voice_id === voiceId;
              const [name] = splitName(voice.name);
              return (
                <div key={voice.voice_id} className={chosen ? "voice selected" : "voice"}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={chosen}
                    className="voice-choose"
                    disabled={disabled}
                    // Roving tabindex: the group is one tab stop and the
                    // arrows move within it, which is the ARIA radiogroup
                    // pattern. Without it, a hundred voices are a hundred
                    // tab stops.
                    tabIndex={chosen || (!selected && index === 0) ? 0 : -1}
                    onClick={() => onVoice(voice.voice_id)}
                    onKeyDown={(event) => move(event, filtered, index, onVoice)}
                  >
                    <span className="voice-face" aria-hidden>
                      {voiceFace(voice)}
                    </span>
                    <span className="voice-text">
                      <span className="voice-name">{name}</span>
                      <span className="voice-labels">{describe(voice)}</span>
                    </span>
                    {chosen && (
                      <span className="voice-chosen" aria-hidden>
                        ✓ Chosen
                      </span>
                    )}
                  </button>
                  {voice.preview ? (
                    <Audition
                      src={api.voicePreview(voice.voice_id)}
                      label={name}
                      disabled={disabled}
                    />
                  ) : (
                    <span className="voice-nopreview">no sample</span>
                  )}
                </div>
              );
            })}
            {!filtered.length && <p className="dim">Nothing matches that.</p>}
          </div>
          {selected?.mock && (
            <p className="note warn flush">
              🧸 This is an offline stand-in, not a real voice. It won't produce audio from
              ElevenLabs.
            </p>
          )}
        </>
      )}
    </div>
  );
}

/** The delivery settings the model honours, and only those.
 *
 *  On v3, speed, similarity and speaker boost do nothing, and a control that
 *  silently has no effect is worse than no control. */
export function Delivery({
  model,
  settings,
  onSettings,
  disabled,
}: {
  model: Model | undefined;
  settings: VoiceSettings;
  onSettings: (next: VoiceSettings) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const honoured = model?.settings_honoured ?? Object.keys(RANGES);

  return (
    <div className="delivery">
      <span className="delivery-title">🎚️ Delivery{model ? ` — ${model.label}` : ""}</span>
      {Object.entries(RANGES).map(([key, range]) => {
        if (!honoured.includes(key)) return null;
        // Only the numeric settings appear in RANGES; the boolean one is
        // rendered as a checkbox below.
        const value = numericSetting(settings, key) ?? defaultFor(key);
        return (
          <div key={key} className="slider">
            <label htmlFor={`${id}-${key}`} className="slider-head">
              <span>{range.label}</span>
              <span className="mono">{value.toFixed(2)}</span>
            </label>
            <input
              id={`${id}-${key}`}
              type="range"
              min={range.min}
              max={range.max}
              step={range.step}
              value={value}
              disabled={disabled}
              onChange={(e) => onSettings({ ...settings, [key]: Number(e.target.value) })}
            />
            <div className="slider-ends" aria-hidden>
              <span>{range.ends[0]}</span>
              <span>{range.ends[1]}</span>
            </div>
          </div>
        );
      })}
      {honoured.includes("use_speaker_boost") && (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={settings.use_speaker_boost ?? true}
            disabled={disabled}
            onChange={(e) => onSettings({ ...settings, use_speaker_boost: e.target.checked })}
          />
          Speaker boost
        </label>
      )}

      {model && honoured.length < Object.keys(RANGES).length + 1 && (
        <p className="note flush">
          {model.label} honours only {honoured.join(", ") || "defaults"}.
          {model.audio_tags && (
            <>
              {" "}
              Pacing and delivery are directed with audio tags instead — try{" "}
              <code>[whispers]</code> or <code>[excited]</code> in the script.
            </>
          )}
        </p>
      )}
    </div>
  );
}

/** A flag for the accents a flag can stand for, else the voice's initial. */
const FLAGS: [string, string][] = [
  ["american", "🇺🇸"],
  ["british", "🇬🇧"],
  ["english", "🇬🇧"],
  ["australian", "🇦🇺"],
  ["irish", "🇮🇪"],
  ["canadian", "🇨🇦"],
  ["indian", "🇮🇳"],
  ["nigerian", "🇳🇬"],
  ["south african", "🇿🇦"],
  ["new zealand", "🇳🇿"],
  ["french", "🇫🇷"],
  ["german", "🇩🇪"],
  ["italian", "🇮🇹"],
  ["spanish", "🇪🇸"],
  ["swedish", "🇸🇪"],
];

export function voiceFace(voice: Voice): string {
  if (voice.mock) return "🧸";
  const accent = (voice.labels.accent ?? "").toLowerCase();
  const flag = FLAGS.find(([name]) => accent.startsWith(name));
  return flag ? flag[1] : (voice.name.trim()[0] ?? "?").toUpperCase();
}

/** "Relatable Everyman · American · calm · young": the tagline from the name,
 *  then the labels. */
export function describe(voice: Voice): string {
  if (voice.mock) return "offline stand-in · no real audio";
  const [, tagline] = splitName(voice.name);
  const text = [tagline, ...Object.values(voice.labels)].filter(Boolean).slice(0, 4).join(" · ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "Brian Robinson - Relatable Everyman": the name, and its tagline. */
export function splitName(full: string): [string, string] {
  const at = full.indexOf(" - ");
  return at < 0 ? [full, ""] : [full.slice(0, at), full.slice(at + 3)];
}

function SearchGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </svg>
  );
}

/** Arrow keys move through the group and select as they go, which is what a
 *  radiogroup does. Home and End jump to the ends. */
function move(
  event: React.KeyboardEvent<HTMLButtonElement>,
  voices: Voice[],
  index: number,
  onVoice: (id: string) => void,
): void {
  const step =
    event.key === "ArrowDown" || event.key === "ArrowRight"
      ? 1
      : event.key === "ArrowUp" || event.key === "ArrowLeft"
        ? -1
        : 0;

  let next = -1;
  if (step) next = (index + step + voices.length) % voices.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = voices.length - 1;
  if (next < 0) return;

  event.preventDefault();
  const target = voices[next];
  if (!target) return;
  onVoice(target.voice_id);
  // Focus follows selection, so the ring lands where the choice did.
  const buttons = event.currentTarget.parentElement?.parentElement?.querySelectorAll<HTMLElement>(
    ".voice-choose",
  );
  buttons?.[next]?.focus();
}

function numericSetting(settings: VoiceSettings, key: string): number | undefined {
  const value = (settings as Record<string, number | boolean | undefined>)[key];
  return typeof value === "number" ? value : undefined;
}

function defaultFor(key: string): number {
  // The provider's own defaults, so an untouched slider matches what it does.
  if (key === "stability") return 0.5;
  if (key === "similarity_boost") return 0.75;
  if (key === "speed") return 1;
  return 0;
}
