import { AppProvider } from "./store";
import { useRoute } from "./router";
import Navbar from "./ui/Navbar";
import Splash from "./ui/Splash";
import Home from "./pages/Home";
import Ask from "./pages/Ask";
import Fishermen from "./pages/Fishermen";
import Safety from "./pages/Safety";
import Zones from "./pages/Zones";
import Route from "./pages/Route";
import Conditions from "./pages/Conditions";
import Alerts from "./pages/Alerts";
import Boundaries from "./pages/Boundaries";
import Replay from "./pages/Replay";
import Agents from "./pages/Agents";
import Data from "./pages/Data";
import Board from "./pages/Board";

const PAGES = { home: Home, fishermen: Fishermen, ask: Ask, safety: Safety, zones: Zones, route: Route, conditions: Conditions, alerts: Alerts, boundaries: Boundaries, replay: Replay, agents: Agents, data: Data, board: Board };

function Shell() {
  const route = useRoute();
  const PageComponent = PAGES[route];
  return (
    <div className="app">
      <a href="#main" className="skip">
        Skip to content
      </a>
      <Splash />
      <Navbar route={route} />
      <PageComponent key={route} />
      <footer className="foot">
        <span>Decision support only. Always follow official IMD and INCOIS advisories.</span>
        <span className="mono">ORCA · SIH 2026 · PS 26176</span>
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
