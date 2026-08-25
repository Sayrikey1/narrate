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
