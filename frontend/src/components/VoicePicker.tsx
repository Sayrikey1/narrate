import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Audition } from "./Audition";
import type { Model, Voice, VoiceSettings } from "../types";

/** Slider ranges. `stability` and `style` are 0–1; `speed` is clamped by the
 *  provider to 0.7–1.2 and going outside that is documented to hurt quality. */
const RANGES: Record<string, { min: number; max: number; step: number; label: string }> = {
  stability: { min: 0, max: 1, step: 0.05, label: "Stability" },
  similarity_boost: { min: 0, max: 1, step: 0.05, label: "Similarity" },
  style: { min: 0, max: 1, step: 0.05, label: "Style" },
  speed: { min: 0.7, max: 1.2, step: 0.05, label: "Speed" },
};

/** Choose a voice and its delivery before anything is generated.
 *
 *  Only the settings the selected model honours are shown — on v3, speed,
 *  similarity and speaker boost do nothing, and a control that silently has no
 *  effect is worse than no control.
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
  const [voices, setVoices] = useState<Voice[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const list = useRef<HTMLDivElement | null>(null);

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
  const honoured = model?.settings_honoured ?? Object.keys(RANGES);

  return (
    <div>
      <div className="field">
        <label>Voice</label>
        {loading && <p className="dim">Loading voices…</p>}
        {error && <p className="error">{error}</p>}
        {!loading && !voices.length && (
          <p className="dim">
            No voices available. Add <code>ELEVEN_API</code> to <code>.env</code>.
          </p>
        )}
        {voices.length > 0 && (
          <>
            <input
              value={query}
              placeholder="Filter by name or accent…"
              onChange={(e) => setQuery(e.target.value)}
              disabled={disabled}
            />
            <div className="voice-list" role="radiogroup" aria-label="Voice" ref={list}>
              {filtered.map((voice, index) => {
                const chosen = voice.voice_id === voiceId;
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
                      <span className="voice-name">
                        {voice.name}
                        {voice.mock && <span className="tag"> offline stand-in</span>}
                      </span>
                      <span className="dim voice-labels">
                        {Object.values(voice.labels).slice(0, 3).join(" · ")}
                      </span>
                    </button>
                    {voice.preview ? (
                      <Audition
                        src={api.voicePreview(voice.voice_id)}
                        label={voice.name}
                        disabled={disabled}
                      />
                    ) : (
                      <span className="faint voice-nopreview">no sample</span>
                    )}
                  </div>
                );
              })}
              {!filtered.length && <p className="dim">Nothing matches that.</p>}
            </div>
            {selected?.mock && (
              <p className="note warn">
                This is an offline stand-in, not a real voice — it will not produce audio
                from the provider.
              </p>
            )}
          </>
        )}
      </div>

      <div className="field">
        <label>Delivery{model ? ` — ${model.label}` : ""}</label>
        {Object.entries(RANGES).map(([key, range]) => {
          if (!honoured.includes(key)) return null;
          // Only the numeric settings appear in RANGES; the boolean one is
          // rendered as a checkbox below.
          const value = numericSetting(settings, key) ?? defaultFor(key);
          return (
            <div key={key} className="slider">
              <span className="dim">{range.label}</span>
              <input
                type="range"
                min={range.min}
                max={range.max}
                step={range.step}
                value={value}
                disabled={disabled}
                onChange={(e) => onSettings({ ...settings, [key]: Number(e.target.value) })}
              />
              <span className="mono">{value}</span>
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
          <p className="note">
            {model.label} honours only {honoured.join(", ") || "defaults"}.
            {model.audio_tags
              ? " Pacing and delivery are directed with audio tags instead."
              : ""}
          </p>
        )}
      </div>
    </div>
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
