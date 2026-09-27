import { useEffect, useState } from "react";
import { OrcaMark } from "./Navbar";

const KEY = "orca.splashSeen";

function seen(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

/** First visit of a session: the mark draws itself and sends one sonar ping, then lifts away (~1.3 s).
 *  Never blocks input, and is skipped entirely under reduced motion. */
export default function Splash() {
  const [show, setShow] = useState(() => !seen() && !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    if (!show) return;
    try {
      sessionStorage.setItem(KEY, "1");
    } catch {
      /* storage unavailable: the splash simply shows again next time */
    }
    const t = window.setTimeout(() => setShow(false), 1500);
    return () => window.clearTimeout(t);
  }, [show]);
  if (!show) return null;
  return (
    <div className="splash" aria-hidden>
      <div className="splash-mark">
        <OrcaMark size={96} />
        <span className="splash-ping" />
        <span className="splash-ping p2" />
      </div>
      <span className="splash-word">ORCA</span>
    </div>
  );
}
