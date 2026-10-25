import { useEffect, useState } from "react";
import { api } from "../api";

/** Peaks fetched this session, by address: a waveform is the same every time
 *  it is drawn, and the server keeps them too. */
const seen = new Map<string, number[]>();

/** The waveform at `url`, or null until it arrives (or if it cannot). */
export function usePeaks(url: string | null): number[] | null {
  const [peaks, setPeaks] = useState<number[] | null>(() => (url ? (seen.get(url) ?? null) : null));

  useEffect(() => {
    if (!url) {
      setPeaks(null);
      return;
    }
    const known = seen.get(url);
    if (known) {
      setPeaks(known);
      return;
    }
    let alive = true;
    api
      .peaks(url)
      .then(({ peaks: fresh }) => {
        seen.set(url, fresh);
        if (alive) setPeaks(fresh);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [url]);

  return peaks;
}

/** Bars, one per peak. The part already played is drawn in the accent, the
 *  rest in the waveform's own grey; a quiet slice keeps a stub so silence
 *  still reads as part of the track. */
export function WaveBars({
  peaks,
  played = 0,
  muted,
  className,
}: {
  peaks: number[];
  /** 0 to 1: how much has been played. */
  played?: number;
  /** Drawn in the off colour: a lane that is switched off. */
  muted?: boolean;
  className?: string;
}) {
  return (
    <span className={["wave-bars", muted ? "muted" : "", className].filter(Boolean).join(" ")} aria-hidden>
      {peaks.map((peak, i) => (
        <span
          key={i}
          className={(i + 0.5) / peaks.length <= played ? "on" : undefined}
          style={{ height: `${16 + Math.max(0, Math.min(1, peak)) * 84}%` }}
        />
      ))}
    </span>
  );
}
