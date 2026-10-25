/** Inline SVG icons. No icon package — the rest of the frontend has no
 *  dependencies beyond React, and a dozen paths do not justify breaking that. */

type Props = { size?: number; className?: string };

const base = (size: number, className?: string) => ({
  width: size,
  height: size,
  viewBox: "0 0 16 16",
  fill: "none" as const,
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  className: ["icon", className].filter(Boolean).join(" "),
  "aria-hidden": true,
});

export const PlayIcon = ({ size = 12, className }: Props) => (
  <svg {...base(size, className)} fill="currentColor" stroke="none">
    <path d="M4.5 3.2v9.6l8-4.8z" />
  </svg>
);

export const PauseIcon = ({ size = 12, className }: Props) => (
  <svg {...base(size, className)} fill="currentColor" stroke="none">
    <rect x="4" y="3.2" width="2.6" height="9.6" rx="0.6" />
    <rect x="9.4" y="3.2" width="2.6" height="9.6" rx="0.6" />
  </svg>
);

export const DownloadIcon = ({ size = 13, className }: Props) => (
  <svg {...base(size, className)}>
    <path d="M8 2v8" />
    <path d="M4.8 7.2 8 10.4l3.2-3.2" />
    <path d="M2.8 13.2h10.4" />
  </svg>
);

export const UploadIcon = ({ size = 20, className }: Props) => (
  <svg {...base(size, className)}>
    <path d="M8 12.6V4.4" />
    <path d="M4.8 7.2 8 4l3.2 3.2" />
    <path d="M2.8 13.6h10.4" />
  </svg>
);

export const WaveIcon = ({ size = 13, className }: Props) => (
  <svg {...base(size, className)}>
    <path d="M2 8h1.4M5 5v6M8 3v10M11 5.5v5M14 8h-.4" />
  </svg>
);

export const SparkIcon = ({ size = 13, className }: Props) => (
  <svg {...base(size, className)}>
    <path d="M8 2.2 9.3 6l3.9 1.2-3.9 1.3L8 13.8 6.7 8.5 2.8 7.2 6.7 6z" />
  </svg>
);

export const CheckIcon = ({ size = 12, className }: Props) => (
  <svg {...base(size, className)}>
    <path d="M3 8.4 6.2 11.6 13 4.8" />
  </svg>
);

export const PlusIcon = ({ size = 13, className }: Props) => (
  <svg {...base(size, className)}>
    <path d="M8 3.2v9.6M3.2 8h9.6" />
  </svg>
);

export const TrashIcon = ({ size = 13, className }: Props) => (
  <svg {...base(size, className)}>
    <path d="M2.6 4.4h10.8M6 4.4V2.8h4v1.6M4.2 4.4l.6 8.4h6.4l.6-8.4" />
  </svg>
);

export const SunIcon = ({ size = 13, className }: Props) => (
  <svg {...base(size, className)}>
    <circle cx="8" cy="8" r="3.1" />
    <path d="M8 1.4v1.5M8 13.1v1.5M1.4 8h1.5M13.1 8h1.5M3.3 3.3l1.1 1.1M11.6 11.6l1.1 1.1M12.7 3.3l-1.1 1.1M4.4 11.6l-1.1 1.1" />
  </svg>
);

export const MoonIcon = ({ size = 13, className }: Props) => (
  <svg {...base(size, className)}>
    <path d="M13 9.6A5.6 5.6 0 0 1 6.4 3a5.6 5.6 0 1 0 6.6 6.6z" />
  </svg>
);

/** Half-filled disc: follow the system, whichever way it is set. */
export const AutoThemeIcon = ({ size = 13, className }: Props) => (
  <svg {...base(size, className)}>
    <circle cx="8" cy="8" r="5.4" />
    <path d="M8 2.6v10.8" />
    <path d="M8 13.4A5.4 5.4 0 0 0 8 2.6z" fill="currentColor" stroke="none" />
  </svg>
);

/* Drawn on a 24-unit grid, as the studio design draws them. */
const grid = (size: number, className?: string, strokeWidth = 2) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none" as const,
  stroke: "currentColor",
  strokeWidth,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  className: ["icon", className].filter(Boolean).join(" "),
  "aria-hidden": true,
});

/** The logo: four bars of a waveform on a lime tile. Lime in both themes —
 *  it is the mark, not the accent drawn for contrast. */
export const BrandMark = ({ size = 34 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 34 34" aria-hidden>
    <rect width="34" height="34" rx="10" fill="var(--ac)" />
    <rect x="8" y="13" width="3" height="8" rx="1.5" fill="#0B0F0D" />
    <rect x="13.5" y="8" width="3" height="18" rx="1.5" fill="#0B0F0D" />
    <rect x="19" y="5" width="3" height="24" rx="1.5" fill="#0B0F0D" />
    <rect x="24.5" y="11" width="3" height="12" rx="1.5" fill="#0B0F0D" />
  </svg>
);

export const ChevronsIcon = ({ size = 16, className }: Props) => (
  <svg {...grid(size, className)}>
    <path d="M8 9l4-4 4 4M8 15l4 4 4-4" />
  </svg>
);

export const ChevronDownIcon = ({ size = 12, className }: Props) => (
  <svg {...grid(size, className, 2.4)}>
    <path d="M6 9l6 6 6-6" />
  </svg>
);

export const CloseIcon = ({ size = 16, className }: Props) => (
  <svg {...grid(size, className)}>
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);

export const SparkleIcon = ({ size = 16, className }: Props) => (
  <svg {...grid(size, className)} fill="currentColor" stroke="none">
    <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />
  </svg>
);

export const PrevIcon = ({ size = 16, className }: Props) => (
  <svg {...grid(size, className)} fill="currentColor" stroke="none">
    <path d="M18 6v12L9 12z M6 6h2v12H6z" />
  </svg>
);

export const NextIcon = ({ size = 16, className }: Props) => (
  <svg {...grid(size, className)} fill="currentColor" stroke="none">
    <path d="M6 6v12l9-6z M16 6h2v12h-2z" />
  </svg>
);

export const SystemThemeIcon = ({ size = 17, className }: Props) => (
  <svg {...grid(size, className, 1.8)}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" />
  </svg>
);

export const LightThemeIcon = ({ size = 17, className }: Props) => (
  <svg {...grid(size, className, 1.8)}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4" />
  </svg>
);

export const DarkThemeIcon = ({ size = 17, className }: Props) => (
  <svg {...grid(size, className, 1.8)}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
  </svg>
);
