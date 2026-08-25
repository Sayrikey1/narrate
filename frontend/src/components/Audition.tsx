import { useEffect, useRef, useState } from "react";
import { claimSolo, releaseSolo } from "../audioBus";
import { PauseIcon, PlayIcon } from "./Icon";

/** A single play/pause control for a short sample.
 *
 *  Deliberately **not** the `Player` component, for two reasons.
 *
 *  `Player` assigns `src` and `preload="metadata"` on mount, which is right for
 *  a handful of takes on a page and catastrophic for a voice list: a workspace
 *  with two hundred voices would fire two hundred requests at
 *  `/api/voices/*\/preview` on render, and each one the server has not cached
 *  yet becomes a fetch from the provider's CDN. Here the element is created on
 *  the **first click**, so a sample is fetched only when somebody asks to hear
 *  it.
 *
 *  And it carries no scrub bar. These are two-second samples; a scrub bar would
 *  be three interactive controls where one will do, in a list where the row
 *  itself is already a control.
 */
export function Audition({
  src,
  label,
  disabled,
}: {
  src: string;
  /** Named in the accessible label, so the control says what it will play. */
  label: string;
  disabled?: boolean;
}) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "playing" | "error">("idle");

  // A stable identity for the bus, so it can silence exactly this control.
  const pause = useRef(() => {
    audio.current?.pause();
    setState((s) => (s === "error" ? s : "idle"));
  }).current;

  useEffect(
    () => () => {
      audio.current?.pause();
      releaseSolo(pause);
      audio.current = null;
    },
    [pause],
  );

  // A changed src is a different sample; drop the element so the next click
  // fetches the new one rather than replaying the old.
  useEffect(() => {
    audio.current?.pause();
    audio.current = null;
    setState("idle");
  }, [src]);

  async function toggle() {
    if (state === "playing") {
      audio.current?.pause();
      releaseSolo(pause);
      setState("idle");
      return;
    }

    let element = audio.current;
    if (!element) {
      element = new Audio(src);
      element.preload = "auto";
      element.addEventListener("ended", () => {
        releaseSolo(pause);
        setState("idle");
      });
      // A pause from anywhere — the bus, the browser — must be reflected, or
      // the button offers "pause" for something that is not playing.
      element.addEventListener("pause", () => setState((s) => (s === "playing" ? "idle" : s)));
      audio.current = element;
    }

    // Silences the transport and any other solo before this one starts.
    claimSolo(pause);
    setState("loading");
    try {
      await element.play();
      setState("playing");
    } catch {
      // Our own endpoint, so a failure here is a real one worth showing: no
      // sample on the provider, an expired link, or no key. The old code
      // pointed straight at a CDN and had no way to tell.
      releaseSolo(pause);
      setState("error");
    }
  }

  const title =
    state === "error" ? `No sample available for ${label}` : `Hear a sample of ${label}`;

  return (
    <button
      type="button"
      className={`audition${state === "playing" ? " playing" : ""}${
        state === "error" ? " failed" : ""
      }`}
      onClick={toggle}
      disabled={disabled || state === "loading"}
      aria-label={title}
      title={title}
    >
      {state === "playing" ? <PauseIcon size={11} /> : <PlayIcon size={11} />}
      <span>{AUDITION_LABEL[state]}</span>
    </button>
  );
}

const AUDITION_LABEL: Record<"idle" | "loading" | "playing" | "error", string> = {
  idle: "Hear it",
  loading: "Loading…",
  playing: "Playing",
  error: "No sample",
};
