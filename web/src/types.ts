// Types mirroring the structured fields of the ORCA API. The UI only renders
// these fields — it never parses the natural-language answer for risk.

export type Level = "LOW" | "MODERATE" | "HIGH" | "SEVERE" | "INSUFFICIENT_DATA";

export interface Place {
  lat: number;
  lon: number;
  label: string;
  source: string;
}

export interface Factor {
  value: number | string | null;
  unit: string | null;
  level: Level;
  label: string;
}

export interface TimelineHour {
  time: string;
  level: Level;
  dominant: string | null;
  factors: Record<string, Factor>;
  missing: string[];
}

export interface FactorAssessment {
  variable: string;
  value: number | string | null;
  unit: string | null;
  level: Level;
  label: string;
  reference: string;
  evidence_id: string | null;
  data_type: string | null;
}

export interface ChangePoint {
  time: string;
  from: Level;
  to: Level;
  direction: "rising" | "easing" | "data";
  cause: FactorAssessment | null;
}

export interface Window {
  start: string;
  end: string;
  hours: number;
  max_level: Level;
}

export interface SafetyCard {
  risk_level: Level;
  valid_from: string;
  valid_until: string;
  timeline: TimelineHour[];
  change_points: ChangePoint[];
  go_windows: Window[];
  caution_windows: Window[];
  key_factors: FactorAssessment[];
  advisories: AdvisorySummary[];
  uncertainty: string[];
  hard_constraints: string[];
  rule_version: string;
  evidence_ids: string[];
}

export interface AdvisorySummary {
  id: string;
  source: string;
  event: string;
  headline: string;
  severity: string;
  onset: string | null;
  expires: string | null;
  area?: string;
  data_type: string;
  reference: string | null;
}

export interface PfzCandidate {
  id: string;
  name: string;
  distance_km: number;
  bearing_deg: number;
  compass: string;
  viable: boolean;
  issues: string[];
  source: string;
  data_type: string;
  demo: boolean;
  valid_from: string | null;
  valid_until: string | null;
  centroid: [number, number];
  attributes: Record<string, unknown>;
}

export interface RouteSegment {
  start: [number, number];
  end: [number, number];
  eta_start: string;
  eta_end: string;
  distance_km: number;
  level: Level;
  dominant: string | null;
}

export interface RoutePlan {
  kind: string;
  feasible: boolean;
  waypoints: { lat: number; lon: number; eta: string }[];
  distance_km: number;
  duration_h: number;
  max_level: Level | null;
  level_hours: Partial<Record<Level, number>>;
  segments: RouteSegment[];
  timeline: { start: string; end: string; level: Level; dominant: string | null }[];
  violations: string[];
  cost: number | null;
}

export interface RouteComparison {
  departure: string;
  speed_knots: number;
  start: [number, number];
  end: [number, number];
  recommended: RoutePlan | null;
  direct: RoutePlan;
  reasons: string[];
  cost_function: string;
  simulated: boolean;
}

export interface GeofenceHit {
  feature_id: string;
  name: string;
  kind: string;
  relation: "inside" | "beyond" | "approaching";
  distance_km: number;
  rule: string;
  accuracy: string;
  authority: string;
}

export interface Evidence {
  id: string;
  source: string;
  product: string;
  data_type: string;
  variable: string | null;
  value: number | string | null;
  unit: string | null;
  lat: number | null;
  lon: number | null;
  valid_time: string | null;
  retrieved_at: string;
  reference: string | null;
  processing: string | null;
}

export interface TraceStep {
  step_id: string;
  agent: string;
  kind: string;
  action: string;
  depends_on: string[];
  started_at: string;
  latency_ms: number | null;
  ok: boolean | null;
  error: string | null;
  sources: string[];
  summary: string;
}

export interface Trace {
  request_id: string;
  conversation_id: string;
  user_query: string;
  language: string | null;
  intents: string[];
  intent_source: string | null;
  plan: { id: string; agent: string; kind: string; action: string; depends_on: string[] }[];
  steps: TraceStep[];
  data_status: DataStatus | null;
  risk_engine_version: string | null;
  llm: Record<string, unknown> | null;
  final_decision: string | null;
  evidence_ids: string[];
  errors: string[];
  latency_ms: number | null;
}

export interface DataStatus {
  mode: string;
  marine_source: "live" | "replay" | "historical" | "none";
  fallback_reason: string | null;
  advisory_sources: string[];
  advisory_errors: string[];
}

export interface SourceInfo {
  source: string;
  product: string;
  data_type: string;
  retrieved_at: string;
  reference: string | null;
  variables: string[];
}

export interface Conditions {
  now: {
    time: string | null;
    wave_height_m: number | null;
    wind_kmh: number | null;
    sst_c: number | null;
    sea_level_m: number | null;
    current_kmh: number | null;
  };
  tides: { type: "high" | "low"; time: string; sea_level_m: number }[];
  max_wave_m: number | null;
  sst_range_c: [number, number] | null;
  max_current_kmh: number | null;
  risk_level?: Level;
  weather: {
    max_wind_kmh: number | null;
    max_gust_kmh: number | null;
    min_visibility_m: number | null;
    total_rain_mm: number | null;
    weather: string[];
    thunderstorm_hours: string[];
  };
}

export interface SourceStatus {
  id: string;
  name: string;
  agency: string;
  variables: string[];
  status: "covers" | "outside" | "not_published" | "not_downloaded" | "found_on_mosdac" | "not_found" | "unchecked";
  detail: string;
}

export interface Discovery {
  lat: number;
  lon: number;
  mode: string;
  sources: SourceStatus[];
  gaps: string[];
  isro: { dataset: string; product: string; granules: number; example: string | null; day: string }[];
}

export interface CompareRow {
  tool: "harbour_safety" | "nearest_zone" | "warnings";
  harbour: string;
  harbour_id: string;
  lat: number;
  lon: number;
  window: { start: string; end: string };
  level?: Level;
  factor?: { variable: string; value: number | string | null } | null;
  go_window?: { start: string; end: string } | null;
  zone?: { name: string; distance_km: number; compass: string } | null;
  warnings?: { event: string; severity: string; headline: string }[];
}

export interface BoardRow {
  harbour: string;
  harbour_id: string;
  state: string;
  lat: number;
  lon: number;
  level: Level;
  factor: { variable: string; value: number | string | null } | null;
  go_window: { start: string; end: string } | null;
  warnings: { event: string; severity: string; headline: string }[];
}

export interface HarbourBoard {
  day: string;
  part: string;
  window: { start: string; end: string };
  as_of: string;
  rules: string;
  counts: Record<string, number>;
  rows: BoardRow[];
  replay: string | null;
}

export interface Cards {
  discovery?: Discovery;
  compare?: { planner: "llm" | "rules"; goal: string; notes: string[]; best: string | null; rows: CompareRow[] };
  safety?: SafetyCard;
  conditions?: Conditions;
  pfz?: { candidates: PfzCandidate[]; provider: string | null; note: string | null };
  route?: RouteComparison | null;
  geofence?: { status: string; hits: GeofenceHit[]; hard_constraints: string[] };
  regulations?: { id: string; title: string; in_effect: boolean; period: string; applies_to: string; reference: string }[];
  alerts?: { covering: AdvisorySummary[]; elsewhere: AdvisorySummary[] };
  hotspots?: { available: boolean; reason?: string; hotspots: { lat: number; lon: number; chl: number; sst: number; front: number; sst_front: boolean }[]; method?: string };
  productivity?: {
    available: boolean;
    reason?: string;
    sst_change_c?: number;
    chl_change_pct?: number;
    sst_recent_c?: number;
    sst_earlier_c?: number;
    chl_recent?: number;
    chl_earlier?: number;
    caveat?: string;
    series?: { date: string; sea_surface_temperature: number; chlorophyll: number }[];
  };
  avoid?: { items: { type: string; id: string; name: string; reasons: string[]; lat?: number; lon?: number; distance_km?: number }[] };
  sources?: SourceInfo[];
}

export interface GeoFeature {
  type: "Feature";
  geometry: { type: "Point" | "LineString" | "Polygon"; coordinates: any };
  properties: Record<string, any>;
}

export interface ChatResponse {
  request_id: string;
  session_id: string;
  language: string;
  language_name: string;
  answer: string;
  answer_source: "llm" | "template";
  answer_note: string | null;
  key_factors: string[];
  actions: string[];
  intents: string[];
  place: Place | null;
  window: { start: string; end: string } | null;
  cards: Cards;
  map: { type: "FeatureCollection"; features: GeoFeature[] };
  evidence: Evidence[];
  data_status: DataStatus | null;
  simulated: boolean;
  clock_offset_hours: number;
  disclaimer: string;
  trace: Trace;
  suggestions: string[];
}

export interface Alert {
  id: string;
  kind: string;
  level: string;
  title: string;
  message: string;
  lat: number;
  lon: number;
  watch_id: string | null;
  vessel_id: string | null;
  created_at: string;
  data_retrieved_at: string | null;
  simulated: boolean;
  evidence_ids: string[];
  source: string;
}

export interface Health {
  status: string;
  version: string;
  data_mode: string;
  last_marine_source: string | null;
  clock: string;
  clock_offset_hours: number;
  scenario: { name: string; title: string; day1_starts: string };
  replay: ReplayInfo | null;
  llm: { provider: string; model: string | null; available: boolean };
  stt?: { available: boolean; engines: string[] };
  tts?: { available: boolean; engine: string | null };
  adapters: { name: string; mode: string; status: string; last_success: string | null; last_error: string | null; last_latency_ms: number | null }[];
  watches: number;
}

export interface Port {
  id: string;
  name: string;
  state: string;
  lat: number;
  lon: number;
  sea_point: [number, number];
}

export interface RiskCell {
  lat: number;
  lon: number;
  level: Level;
  dominant: string | null;
}

export interface ReplayInfo {
  event: string;
  title: string;
  kind: string;
  as_of: string;
  start: string;
  end: string;
  place: { lat: number; lon: number; label: string };
}

export interface ReplayEvent {
  id: string;
  title: string;
  kind: "cyclone" | "calm" | string;
  summary: string;
  region: [number, number, number, number];
  start: string;
  end: string;
  default_as_of: string;
  tags: string[];
  place: { lat: number; lon: number; label: string };
  language_hint: string;
  available: boolean;
  products: Record<string, Record<string, unknown>>;
}

export interface TrackPoint {
  lat: number;
  lon: number;
  valid: string;
  max_wind_kmh: number;
  pressure_hpa: number;
  category: string;
  run: string;
  lead_h: number;
}

export interface ReplayTimeline {
  event: string;
  as_of: string;
  start: string;
  end: string;
  runs: { cycle: string; published: string; known: boolean }[];
  track_observed: TrackPoint[];
  track_forecast: TrackPoint[];
  warnings: {
    id: string;
    sent: string | null;
    event: string;
    headline: string;
    severity: string;
    area: string;
    onset: string | null;
    expires: string | null;
    description: string;
    reference: string | null;
  }[];
}

export interface Grid {
  lat0: number;
  lon0: number;
  dlat: number;
  dlon: number;
  nlat: number;
  nlon: number;
  [key: string]: unknown;
}

export interface FieldLayers {
  valid: string;
  as_of: string;
  wind?: Grid & { u: (number | null)[]; v: (number | null)[]; run: string; lead_h: number; source: string };
  waves?: Grid & { hs: (number | null)[]; run: string; lead_h: number; source: string };
  sst?: Grid & { sst: (number | null)[]; day: string; source: string };
  chl?: Grid & { chl: (number | null)[]; days: string[]; source: string };
}

export interface ConditionsSeries {
  now: string;
  lat: number;
  lon: number;
  series: Record<string, { t: string; v: number | null }[]>;
  levels: { t: string; level: Level; dominant: string | null }[];
  sources: SourceInfo[];
  advisories: { id: string; event: string; headline: string; severity: string; source: string; data_type: string }[];
  data_status: DataStatus;
}

export interface RiskResponse {
  decision: SafetyCard;
  geofence: { status: string; hits: GeofenceHit[]; hard_constraints: string[] };
  evidence: Evidence[];
  data_status: DataStatus;
  simulated: boolean;
}

export interface PfzResponse {
  provider: string | null;
  note: string | null;
  candidates: {
    zone: {
      id: string;
      name: string;
      source: string;
      data_type: string;
      geometry: "polygon" | "line";
      coordinates: [number, number][];
      centroid: [number, number];
      valid_from: string | null;
      valid_until: string | null;
      attributes: Record<string, any>;
      reference: string | null;
    };
    distance_km: number;
    bearing_deg: number;
    compass: string;
    viable: boolean;
    issues: string[];
  }[];
}

export interface BacktestScores {
  site_days: number;
  dangerous_observed: number;
  hits: number;
  misses: number;
  false_alarms: number;
  correct_negatives: number;
  pod: number | null;
  far: number | null;
  missed_danger_rate: number | null;
  exact_level_accuracy: number | null;
  within_one_level: number | null;
}

export interface Backtest {
  generated_at: string;
  rule_version: string;
  season: { from: string; to: string };
  window: string;
  sites: { id: string; name: string; lat: number; lon: number }[];
  matrix: Record<string, Record<string, number>>;
  persistence_matrix: Record<string, Record<string, number>>;
  scores: BacktestScores;
  persistence_scores: BacktestScores;
  rows: { date: string; site: string; forecast: Level; observed: Level; persistence: Level; fc_wave_max: number; obs_wave_max: number; obs_wind_max: number }[];
  method: Record<string, string>;
  sources: string[];
}

export interface GeofenceFeature {
  type: "Feature";
  geometry: { type: "Polygon" | "LineString" | "Point"; coordinates: any };
  properties: { id: string; name: string; kind: string; authority: string; accuracy: string; accuracy_note?: string; rule: string };
}
