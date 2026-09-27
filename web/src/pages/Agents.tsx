import { go } from "../router";
import { useApp } from "../store";
import { EvidenceList } from "../ui/Evidence";
import Page, { Panel } from "../ui/Page";
import TraceView from "../ui/Trace";

const LANES = [
  { name: "You", items: ["Phone or desktop", "Any Indian language"] },
  { name: "Understand", items: ["Language detection", "Intent agent", "Conversation context"] },
  { name: "Plan", items: ["Planner: steps and order", "Specialist agents"] },
  { name: "Decide", items: ["Risk rules", "Route planner", "Geofences", "Zone finder"] },
  { name: "Data", items: ["Catalogue agent", "ISRO INSAT SST (MOSDAC)", "NOAA GFS / GFS-Wave", "OISST · VIIRS", "IMD warnings"] },
  { name: "Explain", items: ["Template or LLM", "Verdict lock", "Evidence"] },
];

export default function Agents() {
  const { active, ask, busy, script, staticDemo } = useApp();
  const samples = staticDemo ? script.slice(0, 3) : ["Is it safe to go fishing tomorrow at 6 AM?", "Show me the safest route to the nearest fishing zone tomorrow at 6 am", "Are there any lightning or cyclone alerts in my area?"];
  return (
    <Page title="How ORCA Decided" blurb="Every answer is a plan of small steps. Rules make the safety decision; the language model only explains it, and a lock rejects any explanation that changes the verdict or invents a number." wide>
      <Panel title="The pipeline" className="pipeline-panel">
        <ol className="pipeline" aria-label="ORCA pipeline">
          {LANES.map((l, i) => (
            <li key={l.name} style={{ animationDelay: `${i * 90}ms` }}>
              <span className="lane-name">{l.name}</span>
              <ul>
                {l.items.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      </Panel>
      {!active ? (
        <Panel title="Ask something to see its plan">
          <div className="preset-list">
            {samples.map((s) => (
              <button key={s} className="ghost" disabled={busy} onClick={() => ask(s)}>
                {s}
              </button>
            ))}
          </div>
          <p className="small muted">
            Or go to <a href="#ask" onClick={(e) => (e.preventDefault(), go("ask"))}>Ask ORCA</a>.
          </p>
        </Panel>
      ) : (
        <div className="agents-grid">
          <Panel title="The question">
            <p className="quote">“{active.trace.user_query}”</p>
            <p className="small">
              Language: <b>{active.language_name}</b> · intents: <b>{active.intents.join(", ") || "—"}</b> · answered in {active.trace.latency_ms} ms
            </p>
            <p className="answer-echo">{active.answer}</p>
          </Panel>
          <Panel title="Steps">
            <TraceView trace={active.trace} />
          </Panel>
          {active.cards.discovery && (
            <Panel title="Data the catalogue agent found" className="discovery-panel">
              <ul className="discovery-list">
                {active.cards.discovery.sources.map((s) => (
                  <li key={s.id} className={`src-${s.status}`}>
                    <span className={`agency agency-${s.agency.toLowerCase().replace(/[^a-z]/g, "")}`}>{s.agency}</span>
                    <span className="src-name">{s.name}</span>
                    <span className="src-status">{s.status.replace(/_/g, " ")}</span>
                    {s.detail && <span className="src-detail small">{s.detail}</span>}
                  </li>
                ))}
              </ul>
              {active.cards.discovery.gaps.length > 0 && <p className="small warn">Gaps: {active.cards.discovery.gaps.join(", ").replace(/_/g, " ")}</p>}
            </Panel>
          )}
          <Panel title={`Evidence (${active.evidence.length})`} className="agents-evidence">
            <EvidenceList evidence={active.evidence} />
          </Panel>
        </div>
      )}
    </Page>
  );
}
