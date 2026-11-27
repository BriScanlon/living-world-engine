// Typed client for the FastAPI backend.

export type Doc = Record<string, any> & { "@id": string; "@type": string; name?: string };

export interface Campaign {
  id: string;
  label: string;
  description: string;
  active: boolean;
}

export type WizardField = {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "checkbox";
  options?: Record<string, string>;
  hint?: string;
  roll?: string;
  min?: number;
  max?: number;
};

export interface WizardSpec {
  approaches: Record<string, { label: string; order: string[]; blurb: string }>;
  focuses: Record<string, { label: string; defaults: WizardAnswers }>;
  steps: Record<string, { title: string; guide: string; fields: WizardField[] }>;
  rolls: Record<string, string[]>;
}

export type WizardAnswers = Record<string, any> & { approach: string; focus: string };

export interface WizardJob {
  id: string;
  status: string;
  events: { seq: number; step: string; kind: string; message: string; data?: any }[];
  result: { campaign: string; name: string; seed: number; atlas: string; town: string; steps: Record<string, { documents: number; message: string }> } | null;
  error: string | null;
}

export interface Health {
  status: string;
  campaign: string;
  default_campaign: string;
  terminusdb: { ok: boolean; version?: string; error?: string };
  mock_llm: boolean;
  llm_model: string | null;
  database: string;
  docling: boolean;
  whisper: boolean;
}

export interface Branch {
  id: string;
  name: string;
  head: string | null;
  is_forecast: boolean;
  run?: {
    session_number: number;
    start_day: number;
    end_day: number;
    created_at: string;
    status: string;
    mock_llm: boolean;
    base_commit: string;
    summary: string;
  };
  live?: { id: string; status: string };
}

export interface Meta {
  branch: string;
  current_day: number;
  last_session: number;
  sessions: string[];
  demo_summary: string;
  counts: Record<string, number>;
}

export interface ProgressEvent {
  seq: number;
  ts: number;
  step: string;
  kind: "start" | "done" | "error" | "complete";
  message: string;
  data?: any;
}

export interface Ref {
  id: string;
  name: string;
}

export interface EventBrief {
  id: string;
  title: string;
  summary: string;
  day: number;
  event_type: string;
  visibility: string;
  actors: Ref[];
  targets: Ref[];
  location: Ref | null;
  caused_by: { id: string; title: string } | null;
  reasoning: string | null;
  reactions?: EventBrief[];
  affected?: (Ref & { type: string })[];
  woken?: { id: string; name: string; distance: number; path: string[] }[];
}

export interface RelShift {
  from: Ref;
  to: Ref;
  before: number | null;
  after: number | null;
  delta: number;
  label_before: string | null;
  label_after: string | null;
  history: { day: number; trust: number; event: string; event_title: string; note: string | null }[];
}

export interface GoalShift {
  id: string;
  name: string;
  owner: Ref;
  is_new: boolean;
  clock_before: number | null;
  clock_after: number;
  segments: number;
  status_before: string | null;
  status_after: string;
  target: Ref | null;
  description: string | null;
}

export interface Rumor {
  id: string;
  text: string;
  dialogue: string;
  speaker: string | null;
  accuracy: "True" | "Exaggerated" | "False";
  day: number;
  location: Ref;
  about_event: { id: string; title: string } | null;
}

export interface Briefing {
  base: string;
  compare: string;
  run: Record<string, any> | null;
  stats: { added: number; changed: number; removed: number; events: number; rumors: number; violations: number };
  ripples: EventBrief[];
  offscreen: EventBrief[];
  shifts: {
    relationships: RelShift[];
    goals: GoalShift[];
    desires: { id: string; agent: Ref; kind: string; target: string; priority: number; change: string; note: string | null }[];
    controls: { change: string; holder: string; kind: string; target: string; day: number; event: string | null }[];
    knowledge: { agent: Ref; learned: { fact: string; kind: string; confidence: number; day: number }[] }[];
    resources: { faction: string; before: number; after: number }[];
    deaths: { id: string; name: string; day: number; event: string }[];
  };
  rumors: Rumor[];
  activations: { id: string; name: string; reason: string; distance: number | null; path: string[]; round: number; goal_ticks: Record<string, number> }[];
  intents: {
    agent_id: string;
    agent_name: string;
    action: string;
    target_id: string | null;
    target_name: string | null;
    resources_spent: number;
    reasoning: string;
    public_or_secret: string;
    responding_to: string | null;
    round: number;
  }[];
  violations: { round: number; message: string }[];
  rounds: [number, number][];
}

export interface Snapshot {
  day: number | null;
  nodes: (Doc & { alive: boolean })[];
  edges: Doc[];
  counts: Record<string, number>;
  names: Record<string, string>;
  max_day: number;
  branch: string;
  commit: string | null;
}

export interface Commit {
  commit: string;
  author: string;
  message: string;
  timestamp: number;
}

export interface IngestJob {
  id: string;
  branch: string;
  status: string;
  sources: string[];
  results: {
    source: string;
    converter: string;
    chunks: number;
    documents_written: number;
    created: Ref[];
    aliases_merged: { surface: string; canonical: string }[];
    edges: number;
    in_world_day: number;
  }[];
  error: string | null;
}

/** A forecast run as the backend remembers it (runs are kept on disk and survive restarts). */
export interface RunSummary {
  id: string;
  branch: string;
  status: "queued" | "running" | "complete" | "failed" | "interrupted";
  error: string | null;
  request: { session_number: number; start_day: number; end_day: number; forecast_days: number };
  created_at: number;
  updated_at: number;
  resumable: boolean;
  next_step: string | null;
  /** Being run by another live process (e.g. scripts/forecast_check.py): watch it, don't resume it. */
  running_elsewhere: boolean;
  owner: string | null;
}

export interface AtlasState {
  atlas: { slug: string; name: string; bbox: number[]; centre: [number, number] | null; layers: string[]; has_tiles: boolean } | null;
  branch: string;
  compare: string | null;
  day: number;
  max_day: number;
  factions: { id: string; name: string; color: string; provinces: number; party: boolean }[];
  provinces: Record<string, { id: string; name: string | null; lon: number | null; lat: number | null; controller: string | null; controller_name: string | null; color: string; changed: boolean; previous: string | null; known: PartyKnows }>;
  view?: AtlasView;
  party?: { name: string | null; where: string | null; where_id?: string | null; visited: number; heard: number; provinces: number } | null;
  places: GeoJSON.FeatureCollection;
  events: GeoJSON.FeatureCollection;
  rumors: GeoJSON.FeatureCollection;
  changed_provinces: number;
  maps?: MapFootprint[];
}

export interface MapFootprint {
  id: string;
  name: string;
  place: string;
  vtt_map_id: string;
  thumb_path: string | null;
  corners: [number, number][];
  width_m: number;
  height_m: number;
}

export interface VttMap {
  id: string;
  title: string;
  source: string | null;
  media_type: string | null;
  width: number | null;
  height: number | null;
  thumb: string | null;
}

export interface LinkedMap {
  id: string;
  name: string;
  kind: string | null;
  vtt_map_id: string;
  from_day: number | null;
  thumb: string | null;
}

export type AtlasView = "dm" | "party";
/** What the party knows of a place: been there, heard of it, or nothing (fog). */
export type PartyKnows = "visited" | "heard" | null;

export interface PlaceDetail {
  id: string;
  type: string;
  name: string;
  description: string | null;
  kind: string | null;
  population: number | null;
  controller: Ref | null;
  province: Ref | null;
  people: { id: string; name: string; role: string | null; species?: string | null; char_class?: string | null; srd_ref?: string | null }[];
  monsters?: { id: string; name: string; challenge: string | null; srd_ref: string | null }[];
  events: { id: string; title: string; day: number; summary: string }[];
  rumors: { dialogue: string; accuracy: string; speaker: string | null }[];
  places: { id: string; name: string; kind: string | null }[];
  maps?: LinkedMap[];
  party?: { state: PartyKnows; here: boolean; marked: "KNOWS" | "BELIEVES" | null } | null;
  view?: AtlasView;
  travel?: Travel | null;
}

/** How far a place is from where the party is, by the quickest route (roads where they help). */
export interface Travel {
  from: string;
  from_id: string;
  km: number;
  road_km: number;
  sea_km: number;
  straight_km: number;
  days: { foot: number; horse: number; army: number; rumor: number };
  path: [number, number][];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

const enc = encodeURIComponent;

export const api = {
  health: () => req<Health>("/health"),
  meta: (branch = "main") => req<Meta>(`/meta?branch=${enc(branch)}`),
  branches: () => req<Branch[]>("/branches"),
  log: (branch: string, limit = 50) => req<Commit[]>(`/branches/${branch}/log?limit=${limit}`),
  merge: (branch: string) => req<any>(`/branches/${branch}/merge`, json({})),
  cherryPick: (branch: string, eventIds: string[]) =>
    req<{ picked: string[]; count: number }>(`/branches/${branch}/cherry-pick`, json({ event_ids: eventIds })),
  discard: (branch: string) => req<any>(`/branches/${branch}`, { method: "DELETE" }),
  rerun: (branch: string) => req<{ run_id: string; branch: string }>(`/branches/${branch}/rerun`, { method: "POST" }),
  diff: (base: string, compare: string) => req<Briefing>(`/diff?base=${enc(base)}&compare=${enc(compare)}`),
  rawDiff: (base: string, compare: string) => req<any[]>(`/diff/raw?base=${enc(base)}&compare=${enc(compare)}`),
  snapshot: (branch: string, day: number | null, commit?: string | null) =>
    req<Snapshot>(
      `/world/snapshot?branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}${commit ? `&commit=${enc(commit)}` : ""}`,
    ),
  agent: (agentId: string, branch: string, day: number | null) =>
    req<any>(`/world/agent/${agentId}?branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}`),
  forecast: (body: {
    session_number: number;
    start_day: number;
    end_day: number;
    forecast_days: number;
    session_summary: string;
    base_branch: string;
  }) => req<{ run_id: string; branch: string }>("/forecasts", json(body)),
  forecasts: () => req<RunSummary[]>("/forecasts"),
  resumeForecast: (id: string) => req<{ run_id: string; branch: string }>(`/forecasts/${id}/resume`, { method: "POST" }),
  seed: () => req<any>("/admin/seed?reset=true", { method: "POST" }),
  ingestText: (body: { title: string; text: string; branch: string; is_session: boolean; in_world_day: number | null }) =>
    req<IngestJob>("/ingest/text", json(body)),
  ingestFiles: (files: File[], branch: string, isSession: boolean, day: number | null) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("branch", branch);
    fd.append("is_session", String(isSession));
    if (day !== null) fd.append("in_world_day", String(day));
    return req<IngestJob>("/ingest/files", { method: "POST", body: fd });
  },
  transcribe: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ text: string }>("/ingest/transcribe", { method: "POST", body: fd });
  },
  job: (id: string) => req<IngestJob>(`/ingest/jobs/${id}`),
  atlas: (branch: string, day: number | null, compare: string | null, view: AtlasView = "dm") =>
    req<AtlasState>(
      `/atlas?branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}${compare ? `&compare=${enc(compare)}` : ""}&view=${view}`,
    ),
  place: (id: string, branch: string, day: number | null, view: AtlasView = "dm") =>
    req<PlaceDetail>(`/atlas/place/${id}?branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}&view=${view}`),
  markParty: (body: { place: string; state: "visited" | "heard" | "unknown"; branch: string; day: number | null }) =>
    req<{ place: string; state: string; day: number; changed: boolean; table_refreshed: boolean }>("/party/knowledge", json(body)),
  partyMapUrl: (branch: string, day: number | null) =>
    `/api/party/map.webp?branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}&t=${Date.now()}`,
  partyMapToTable: (body: { branch: string; day: number | null }) =>
    req<{ title: string; vtt_map_id: string; replaced: number; places: { visited: number; heard: number } }>(
      "/party/map-to-table", json(body)),
  atlasFile: (slug: string, name: string) => `/api/atlas/files/${slug}/${name}`,
  campaigns: () => req<Campaign[]>("/campaigns"),
  activateCampaign: (id: string) => req<{ active: string }>("/campaigns/active", json({ id })),
  deleteCampaign: (id: string) => req<any>(`/campaigns/${enc(id)}`, { method: "DELETE" }),
  wizardSpec: () => req<WizardSpec>("/wizard/spec"),
  wizardBuild: (answers: WizardAnswers) => req<WizardJob>("/wizard/build", json(answers)),
  wizardJob: (id: string) => req<WizardJob>(`/wizard/jobs/${id}`),
  wizardUpload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ upload_id: string; name: string; states: number }>("/wizard/upload", { method: "POST", body: fd });
  },
  srdEntry: (id: string) => req<any>(`/srd/entry/${id}`),
  vttStatus: () => req<{ configured: boolean; reachable: boolean; url: string | null; error?: string }>("/vtt/status"),
  vttMaps: () => req<VttMap[]>("/vtt/maps"),
  linkMap: (placeId: string, body: { vtt_map_id: string; branch: string; day: number | null }) =>
    req<{ map: string }>(`/places/${placeId}/maps`, json(body)),
  unlinkMap: (mapId: string, branch: string, day: number | null) =>
    req<any>(`/maps/${mapId}?branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}`, { method: "DELETE" }),
  toTable: (placeId: string, body: { branch: string; day: number | null; map_doc_id?: string | null }) =>
    req<{ map: LinkedMap | null; scene: { place: string; people: number; rumors: number; creatures: number } }>(
      `/places/${placeId}/to-table`, json(body)),
  srdInfo: () => req<{ attribution: string; license: string; counts: Record<string, number> }>("/srd"),
  mapBrief: (placeId: string, branch: string, day: number | null) =>
    req<MapBrief>(`/places/${placeId}/map-brief?branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}`),
  paintMap: (placeId: string, body: { branch: string; day: number | null; description?: string; animate?: boolean }) =>
    req<{ job: string; brief: MapBrief }>(`/places/${placeId}/paint-map`, json(body)),
  collectPaint: (jobId: string) => req<PaintStatus>(`/vtt/jobs/${jobId}/collect`),
  paintTokens: (placeId: string, body: { branch: string; day: number | null }) =>
    req<{ queued: { name: string; job: string }[]; already: string[] }>(`/places/${placeId}/tokens`, json(body)),
  tableLog: () => req<TableLog>("/vtt/session-log"),
  localBrief: (lon: number, lat: number, branch: string, day: number | null) =>
    req<LocalBrief>(`/atlas/local-brief?lon=${lon}&lat=${lat}&branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}`),
  paintLocal: (body: { lon: number; lat: number; branch: string; day: number | null; name?: string; description?: string }) =>
    req<{ place: string; name: string; job: string }>("/atlas/local-map", json(body)),
};

/** A spot on the globe, described from the world around it (for a zoom-in battle map). */
export interface LocalBrief {
  name: string;
  description: string;
  day: number;
  existing: string | null;
  look: { lon: number; lat: number; on_land: boolean; nearest?: { name: string; km: number; direction: string } };
}

export interface MapBrief {
  place: string;
  branch: string;
  day: number;
  title: string;
  description: string;
  events: string[];
}

export interface PaintStatus {
  job: string;
  status: "queued" | "running" | "done" | "error";
  progress: number | null;
  message: string | null;
  error: string | null;
  linked: { map: string; place: string; day: number } | null;
}

export interface TableLog {
  summary: string;
  entries: number;
  notes: number;
  places: string[];
  start_day: number | null;
  end_day: number | null;
}
