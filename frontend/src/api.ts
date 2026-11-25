// Typed client for the FastAPI backend.

export type Doc = Record<string, any> & { "@id": string; "@type": string; name?: string };

export interface Health {
  status: string;
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

export interface AtlasState {
  atlas: { slug: string; name: string; bbox: number[]; centre: [number, number] | null; layers: string[]; has_tiles: boolean } | null;
  branch: string;
  compare: string | null;
  day: number;
  max_day: number;
  factions: { id: string; name: string; color: string; provinces: number; party: boolean }[];
  provinces: Record<string, { id: string; name: string; lon: number | null; lat: number | null; controller: string | null; controller_name: string | null; color: string; changed: boolean; previous: string | null }>;
  places: GeoJSON.FeatureCollection;
  events: GeoJSON.FeatureCollection;
  rumors: GeoJSON.FeatureCollection;
  changed_provinces: number;
}

export interface PlaceDetail {
  id: string;
  type: string;
  name: string;
  description: string | null;
  kind: string | null;
  population: number | null;
  controller: Ref | null;
  province: Ref | null;
  people: { id: string; name: string; role: string | null }[];
  events: { id: string; title: string; day: number; summary: string }[];
  rumors: { dialogue: string; accuracy: string; speaker: string | null }[];
  places: { id: string; name: string; kind: string | null }[];
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
  forecasts: () => req<any[]>("/forecasts"),
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
  atlas: (branch: string, day: number | null, compare: string | null) =>
    req<AtlasState>(
      `/atlas?branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}${compare ? `&compare=${enc(compare)}` : ""}`,
    ),
  place: (id: string, branch: string, day: number | null) =>
    req<PlaceDetail>(`/atlas/place/${id}?branch=${enc(branch)}${day !== null ? `&day=${day}` : ""}`),
  atlasFile: (slug: string, name: string) => `/api/atlas/files/${slug}/${name}`,
};
