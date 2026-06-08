import type { ReactElement, SVGProps } from "react";

/**
 * Small inline stroke icons (currentColor) used across the portal shell.
 * Kept dependency-free; each icon shares a 24x24 viewBox.
 */
function Glyph(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    />
  );
}

export function MenuIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M4 6h16" />
      <path d="M4 12h16" />
      <path d="M4 18h16" />
    </Glyph>
  );
}

export function HomeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V21h14V9.5" />
    </Glyph>
  );
}

export function ShieldIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6z" />
    </Glyph>
  );
}

export function UserIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-3.6 3.6-6 8-6s8 2.4 8 6" />
    </Glyph>
  );
}

export function SlidersIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M4 7h9" />
      <path d="M17 7h3" />
      <circle cx="15" cy="7" r="2" />
      <path d="M4 17h3" />
      <path d="M11 17h9" />
      <circle cx="9" cy="17" r="2" />
    </Glyph>
  );
}

export function LogOutIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9" />
    </Glyph>
  );
}

// --- Per-team glyphs -------------------------------------------------------

export function RadarIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M12 12 7 5.5" />
      <path d="M19.5 12a7.5 7.5 0 1 1-4-6.6" />
      <path d="M12 12l5-2.5" />
      <circle cx="12" cy="12" r="1.4" />
    </Glyph>
  );
}

export function CrosshairIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="12" r="7" />
      <path d="M12 2v4" />
      <path d="M12 18v4" />
      <path d="M2 12h4" />
      <path d="M18 12h4" />
    </Glyph>
  );
}

export function GlobeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3c2.5 2.6 2.5 15.4 0 18" />
      <path d="M12 3c-2.5 2.6-2.5 15.4 0 18" />
    </Glyph>
  );
}

export function SearchIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="11" cy="11" r="6" />
      <path d="m20 20-3.5-3.5" />
    </Glyph>
  );
}

export function BarChartIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M4 20h16" />
      <path d="M7 20v-6" />
      <path d="M12 20V8" />
      <path d="M17 20v-9" />
    </Glyph>
  );
}

export function SwordsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M14.5 3H20v5.5L9 19.5l-4.5-4.5L14.5 3Z" />
      <path d="M4 16.5 7.5 20" />
      <path d="M14 10 4 3" />
    </Glyph>
  );
}

export function CogIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3" />
      <path d="M12 19v3" />
      <path d="m4.2 4.2 2.1 2.1" />
      <path d="m17.7 17.7 2.1 2.1" />
      <path d="M2 12h3" />
      <path d="M19 12h3" />
      <path d="m4.2 19.8 2.1-2.1" />
      <path d="m17.7 6.3 2.1-2.1" />
    </Glyph>
  );
}

export function CheckIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M20 6 9 17l-5-5" />
    </Glyph>
  );
}

export function XIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M18 6 6 18" />
      <path d="M6 6l12 12" />
    </Glyph>
  );
}

export function PlusIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </Glyph>
  );
}

export function InfoIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5" />
      <path d="M12 8h.01" />
    </Glyph>
  );
}

export function ListIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <Glyph {...props}>
      <path d="M8 6h12" />
      <path d="M8 12h12" />
      <path d="M8 18h12" />
      <path d="M4 6h.01" />
      <path d="M4 12h.01" />
      <path d="M4 18h.01" />
    </Glyph>
  );
}

/**
 * GitHub mark. Unlike the stroke {@link Glyph} icons this is a filled
 * silhouette, so it is authored as a standalone filled SVG using currentColor.
 */
export function GithubIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      <path d="M12 1.5a10.5 10.5 0 0 0-3.32 20.46c.52.1.71-.23.71-.5v-1.95c-2.9.63-3.52-1.24-3.52-1.24-.48-1.2-1.16-1.52-1.16-1.52-.95-.65.07-.64.07-.64 1.05.07 1.6 1.08 1.6 1.08.93 1.6 2.45 1.13 3.05.87.1-.68.36-1.13.66-1.39-2.32-.26-4.76-1.16-4.76-5.16 0-1.14.41-2.07 1.08-2.8-.11-.27-.47-1.34.1-2.79 0 0 .88-.28 2.88 1.07a9.9 9.9 0 0 1 5.24 0c2-1.35 2.88-1.07 2.88-1.07.57 1.45.21 2.52.1 2.79.67.73 1.08 1.66 1.08 2.8 0 4.01-2.45 4.9-4.78 5.16.37.32.71.95.71 1.92v2.85c0 .28.19.61.72.5A10.5 10.5 0 0 0 12 1.5Z" />
    </svg>
  );
}

/**
 * Map a team name to its glyph. Names are matched exactly against the canonical
 * team list; anything unknown falls back to a neutral shield so new teams still
 * render a sensible icon.
 */
const TEAM_ICONS: Record<
  string,
  (props: SVGProps<SVGSVGElement>) => ReactElement
> = {
  "Detect and Response": RadarIcon,
  "Threat Hunting": CrosshairIcon,
  "Threat Intel": GlobeIcon,
  "Forensics & BID": SearchIcon,
  "Advanced Analytics": BarChartIcon,
  "Red Team": SwordsIcon,
  "Threat Detection Engineering": CogIcon,
};

export function teamIcon(
  name: string,
): (props: SVGProps<SVGSVGElement>) => ReactElement {
  return TEAM_ICONS[name] ?? ShieldIcon;
}
