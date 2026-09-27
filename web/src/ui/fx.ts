// Pointer effects shared by every page, installed once:
//  • sonar ripple — a ring spreads from the exact point a button or tile is pressed (CSS: .rippling::after);
//  • chart lamp  — panels and tiles carry a soft light that follows the pointer (CSS vars --mx / --my).
// Both only set classes and CSS variables; the look lives in styles.css and is off under reduced motion.

const PRESSABLE = "button:not(:disabled), .station, .event-card, .sheet-grid a";
const LIT = ".panel, .station, .bridge-card, .event-card";

export function installFx(): void {
  if (typeof window === "undefined") return;

  document.addEventListener(
    "pointerdown",
    (e) => {
      const el = (e.target as Element | null)?.closest?.(PRESSABLE) as HTMLElement | null;
      if (!el) return;
      const r = el.getBoundingClientRect();
      el.style.setProperty("--rx", `${e.clientX - r.left}px`);
      el.style.setProperty("--ry", `${e.clientY - r.top}px`);
      el.style.setProperty("--rs", `${Math.hypot(r.width, r.height) * 2}px`);
      el.classList.remove("rippling");
      void el.offsetWidth; // restart the animation on quick repeat presses
      el.classList.add("rippling");
      window.setTimeout(() => el.classList.remove("rippling"), 700);
    },
    { passive: true },
  );

  let frame = 0;
  document.addEventListener(
    "pointermove",
    (e) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const el = (e.target as Element | null)?.closest?.(LIT) as HTMLElement | null;
        if (!el) return;
        const r = el.getBoundingClientRect();
        el.style.setProperty("--mx", `${e.clientX - r.left}px`);
        el.style.setProperty("--my", `${e.clientY - r.top}px`);
      });
    },
    { passive: true },
  );
}
