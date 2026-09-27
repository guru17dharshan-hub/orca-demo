import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./sonar.css";
import { STATIC_DEMO } from "./demo";
import { installFx } from "./ui/fx";

installFx();

// Installable app + last answers offline (production server only; the static preview has no backend).
if (import.meta.env.PROD && !STATIC_DEMO && "serviceWorker" in navigator && location.protocol.startsWith("http")) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
