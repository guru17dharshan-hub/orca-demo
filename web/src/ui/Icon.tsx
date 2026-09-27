// ORCA's own line-icon set: one 24 px grid, 1.7 stroke, round joins, nautical metaphors. Each icon names its
// moving part (.i-spin, .i-beam, .i-swim …) so CSS can give it a signature motion on hover — the lifebuoy turns,
// the lighthouse flashes, the fish swims — and the page emblem draws it in (every stroke has pathLength=1).
import type { ReactElement } from "react";
import type { RouteId } from "../router";

export type IconName =
  | RouteId
  | "mic"
  | "stop"
  | "speaker"
  | "send"
  | "sun"
  | "moon"
  | "auto"
  | "menu"
  | "down"
  | "retry"
  | "wave";

const P = { pathLength: 1 } as const;

const SHAPES: Record<IconName, ReactElement> = {
  home: (
    <>
      <circle {...P} cx="12" cy="12" r="9" />
      <g className="i-spin">
        <path {...P} d="M12 4.5l2.3 7.5L12 19.5 9.7 12z" />
        <path {...P} d="M12 4.5l2.3 7.5h-4.6z" className="i-fill" />
      </g>
    </>
  ),
  fishermen: (
    <>
      <g className="i-bob">
        <path {...P} d="M3 14.5h18l-2.8 4.5H5.8z" />
        <path {...P} d="M9 14.5V5.5M9 6l8.5 5.5" />
        <path {...P} className="i-line" d="M17.5 11.5v2.2a1.1 1.1 0 0 1-2.2 0" />
      </g>
      <path {...P} className="i-wave" d="M2.5 21.5c1.6-1.2 3.2-1.2 4.8 0s3.2 1.2 4.8 0 3.2-1.2 4.8 0 3.2 1.2 4.8 0" />
    </>
  ),
  board: (
    <>
      <path {...P} d="M5 4.5h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-14a1 1 0 0 1 1-1z" />
      <path {...P} className="i-l1" d="M8 9h8" />
      <path {...P} className="i-l2" d="M8 12.5h5" />
      <path {...P} className="i-l3" d="M8 16h7" />
      <circle cx="17.5" cy="12.5" r="1.2" className="i-dot" />
    </>
  ),
  ask: (
    <>
      <path {...P} d="M4 4.5h16a1.5 1.5 0 0 1 1.5 1.5v9.5A1.5 1.5 0 0 1 20 17H10.5L6 20.5V17H4a1.5 1.5 0 0 1-1.5-1.5V6A1.5 1.5 0 0 1 4 4.5z" />
      <path {...P} className="i-wave" d="M6 11c1.2-1.5 2.4-1.5 3.6 0s2.4 1.5 3.6 0 2.4-1.5 3.6 0" />
    </>
  ),
  safety: (
    <>
      <circle {...P} cx="12" cy="12" r="9" />
      <circle {...P} cx="12" cy="12" r="4" />
      <path {...P} className="i-spin" d="M5.6 5.6l3.6 3.6M18.4 5.6l-3.6 3.6M5.6 18.4l3.6-3.6M18.4 18.4l-3.6-3.6" />
    </>
  ),
  zones: (
    <>
      <path {...P} className="i-ring" d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5" />
      <g className="i-swim">
        <path {...P} d="M6.5 12c1.8-2.8 4.4-3.8 7.2-3.8 2.3 0 3.9 1.6 4.8 3.8-.9 2.2-2.5 3.8-4.8 3.8-2.8 0-5.4-1-7.2-3.8z" />
        <path {...P} d="M6.5 12L4 9.6v4.8z" />
        <circle cx="15.6" cy="11.2" r=".9" className="i-dot" />
      </g>
    </>
  ),
  route: (
    <>
      <path {...P} className="i-dash" d="M6.2 17.6c2.6-4.6 9.4-2.6 11.3-7.6" />
      <circle {...P} cx="5" cy="19" r="2" />
      <path {...P} className="i-flag" d="M17.5 10V3l4 1.8-4 1.8" />
    </>
  ),
  conditions: (
    <>
      <path {...P} className="i-gust" d="M3 6.5h10.5A2.3 2.3 0 1 0 11.2 4.2" />
      <path {...P} className="i-gust i-gust2" d="M3 10.5h14.5a2.3 2.3 0 1 1-2.3 2.3" />
      <path {...P} className="i-wave" d="M2.5 18.5c1.6-1.8 3.2-1.8 4.8 0s3.2 1.8 4.8 0 3.2-1.8 4.8 0 3.2 1.8 4.8 0" />
    </>
  ),
  alerts: (
    <>
      <path {...P} d="M10 21l1-11.5h2L14 21z" />
      <path {...P} d="M9.8 9.5h4.4M10.6 9.5V6.8h2.8v2.7M10.4 6.8L12 5l1.6 1.8" />
      <path {...P} d="M7 21h10" />
      <path {...P} className="i-beam" d="M15.8 7.2l5.2-2.2M15.8 8.6l5.2 2.2M8.2 7.2L3 5M8.2 8.6L3 10.8" />
    </>
  ),
  boundaries: (
    <>
      <g className="i-bob">
        <path {...P} d="M8.5 16h7l-1.4-5.5H9.9z" />
        <path {...P} d="M10.4 10.5L12 6.4l1.6 4.1" />
        <circle {...P} cx="12" cy="4.6" r="1.3" className="i-lamp" />
      </g>
      <path {...P} className="i-wave" d="M2.5 19.5c1.6-1.3 3.2-1.3 4.8 0s3.2 1.3 4.8 0 3.2-1.3 4.8 0 3.2 1.3 4.8 0" />
    </>
  ),
  replay: (
    <>
      <path {...P} d="M3.8 12a8.2 8.2 0 1 0 2.4-5.8" />
      <path {...P} d="M3.4 3.6v3.6H7" />
      <path {...P} className="i-hands" d="M12 7.6V12l3 2" />
    </>
  ),
  agents: (
    <>
      <path {...P} d="M6.6 7.4L10.6 10.6M17.4 7.4l-4 3.2M12 13.8v2.6" />
      <circle {...P} cx="5.2" cy="6.2" r="2" className="i-node n1" />
      <circle {...P} cx="18.8" cy="6.2" r="2" className="i-node n2" />
      <circle {...P} cx="12" cy="12" r="2" className="i-node n3" />
      <circle {...P} cx="12" cy="18.6" r="2" className="i-node n4" />
    </>
  ),
  data: (
    <>
      <path {...P} className="i-l1" d="M12 3l9 4.5L12 12 3 7.5z" />
      <path {...P} className="i-l2" d="M3 12l9 4.5 9-4.5" />
      <path {...P} className="i-l3" d="M3 16.5L12 21l9-4.5" />
    </>
  ),
  mic: (
    <>
      <path {...P} d="M9 6a3 3 0 0 1 6 0v5.5a3 3 0 0 1-6 0z" />
      <path {...P} d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6" />
    </>
  ),
  stop: <rect x="6.5" y="6.5" width="11" height="11" rx="2.5" className="i-solid" />,
  speaker: (
    <>
      <path {...P} d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
      <path {...P} className="i-sound s1" d="M15.5 9a4 4 0 0 1 0 6" />
      <path {...P} className="i-sound s2" d="M18.2 6.4a7.6 7.6 0 0 1 0 11.2" />
    </>
  ),
  send: (
    <>
      <path {...P} className="i-sail" d="M12 3v12.5M12 4l6.5 11.5H12" />
      <path {...P} d="M3.5 17.5h17l-2.6 3.2H6.1z" />
    </>
  ),
  sun: (
    <>
      <circle {...P} cx="12" cy="12" r="4" />
      <path {...P} className="i-spin" d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" />
    </>
  ),
  moon: <path {...P} d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z" />,
  auto: (
    <>
      <circle {...P} cx="12" cy="12" r="8.5" />
      <path d="M12 3.5a8.5 8.5 0 0 1 0 17z" className="i-solid" />
    </>
  ),
  menu: (
    <>
      <path {...P} className="i-wave" d="M3.5 7c1.4-1.3 2.8-1.3 4.2 0s2.8 1.3 4.3 0 2.8-1.3 4.2 0 2.8 1.3 4.3 0" />
      <path {...P} d="M3.5 12h17" />
      <path {...P} className="i-wave i-wave2" d="M3.5 17c1.4-1.3 2.8-1.3 4.2 0s2.8 1.3 4.3 0 2.8-1.3 4.2 0 2.8 1.3 4.3 0" />
    </>
  ),
  down: <path {...P} className="i-drop" d="M12 4v15M6 13l6 6 6-6" />,
  retry: (
    <>
      <path {...P} className="i-spin" d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4.5h-4.5" />
    </>
  ),
  wave: <path {...P} className="i-wave" d="M2.5 12c1.6-1.8 3.2-1.8 4.8 0s3.2 1.8 4.8 0 3.2-1.8 4.8 0 3.2 1.8 4.8 0" />,
};

export default function Icon({ name, size = 20, className = "", title }: { name: IconName; size?: number; className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={`ic ic-${name} ${className}`}
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {SHAPES[name]}
    </svg>
  );
}
