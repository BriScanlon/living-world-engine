import { useEffect, useMemo, useRef, useState } from "react";
import { api, Branch, Commit, Doc, Snapshot } from "../api";
import { Badge, Card, cx, Empty, ErrorBox, Field, inputCls, Spinner, TrustBar } from "../ui";

const TYPE_COLOR: Record<string, string> = {
  Character: "#f0ad4e",
  Faction: "#e0607e",
  Location: "#4fb3d9",
  Item: "#9b8cff",
  Monster: "#7bc96f",
  Goal: "#c9d0da",
  Event: "#7d8796",
  Rumor: "#5d6573",
};
const GRAPH_TYPES = ["Character", "Faction", "Location", "Item", "Monster"];

type P = { x: number; y: number };

function layout(nodes: Doc[], links: [string, string][], prev: Map<string, P>, w: number, h: number) {
  const pos = new Map<string, P & { vx: number; vy: number }>();
  nodes.forEach((n, i) => {
    const p = prev.get(n["@id"]);
    const a = (i / Math.max(1, nodes.length)) * Math.PI * 2;
    pos.set(n["@id"], { x: p?.x ?? w / 2 + Math.cos(a) * w * 0.35, y: p?.y ?? h / 2 + Math.sin(a) * h * 0.35, vx: 0, vy: 0 });
  });
  // Fruchterman–Reingold with cooling, then fit the result into the viewport.
  const ids = [...pos.keys()];
  const k = Math.sqrt((w * h) / Math.max(1, ids.length)) * 0.6;
  const ITER = 300;
  const seen = new Set<string>();
  links = links.filter(([s, t]) => {
    const key = s < t ? `${s}|${t}` : `${t}|${s}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  for (let it = 0; it < ITER; it++) {
    const temp = (w / 10) * (1 - it / ITER) + 0.5;
    for (let i = 0; i < ids.length; i++) {
      const a = pos.get(ids[i])!;
      for (let j = i + 1; j < ids.length; j++) {
        const b = pos.get(ids[j])!;
        const dx = a.x - b.x, dy = a.y - b.y;
        const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        const f = (k * k) / d;
        a.vx += (dx / d) * f; a.vy += (dy / d) * f;
        b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
      }
    }
    for (const [s, t] of links) {
      const a = pos.get(s), b = pos.get(t);
      if (!a || !b || a === b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const f = (d * d) / k / 5;
      a.vx += (dx / d) * f; a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
    for (const p of pos.values()) {
      p.vx += (w / 2 - p.x) * 0.12; p.vy += (h / 2 - p.y) * 0.12;
      const sp = Math.sqrt(p.vx * p.vx + p.vy * p.vy) || 1;
      const step = Math.min(sp, temp);
      p.x += (p.vx / sp) * step; p.y += (p.vy / sp) * step;
      p.vx = 0; p.vy = 0;
    }
  }
  const xs = [...pos.values()].map((p) => p.x), ys = [...pos.values()].map((p) => p.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const padX = 80, padY = 32;
  for (const p of pos.values()) {
    p.x = padX + ((p.x - x0) / Math.max(1, x1 - x0)) * (w - 2 * padX);
    p.y = padY + ((p.y - y0) / Math.max(1, y1 - y0)) * (h - 2 * padY);
  }
  return pos;
}

export default function TimeTravel({ branches }: { branches: Branch[] }) {
  const [branch, setBranch] = useState("main");
  const [day, setDay] = useState<number | null>(null);
  const [commits, setCommits] = useState<Commit[]>([]);
  const [commit, setCommit] = useState<string>("");
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [agentView, setAgentView] = useState<any>(null);
  const [edgeKinds, setEdgeKinds] = useState<Set<string>>(new Set(["RelationshipEdge", "ControlEdge"]));
  const [maxDay, setMaxDay] = useState(30);
  const prevPos = useRef(new Map<string, P>());

  useEffect(() => {
    api.log(branch, 100).then(setCommits).catch(() => setCommits([]));
    setCommit("");
  }, [branch]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.snapshot(branch, day, commit || null)
      .then((s) => {
        setSnap(s);
        setMaxDay(Math.max(s.max_day, 1));
        if (day === null) setDay(s.max_day);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [branch, day, commit]);

  useEffect(() => {
    if (!selected || !snap) return setAgentView(null);
    const t = selected.split("/")[0];
    if (t === "Character" || t === "Faction") api.agent(selected, branch, day).then(setAgentView).catch(() => setAgentView(null));
    else setAgentView(null);
  }, [selected, branch, day, snap]);

  const W = 900, H = 560;
  const graph = useMemo(() => {
    if (!snap) return null;
    const nodes = snap.nodes.filter((n) => GRAPH_TYPES.includes(n["@type"]));
    const ids = new Set(nodes.map((n) => n["@id"]));
    const edges = snap.edges
      .filter((e) => edgeKinds.has(e["@type"]))
      .map((e) => {
        const s = e.from_id ?? e.agent_id, d = e.to_id ?? e.target_id ?? e.fact_or_event_id;
        return { e, s, d };
      })
      .filter((x) => ids.has(x.s) && ids.has(x.d));
    const structural: [string, string][] = [];
    for (const n of nodes) for (const k of ["location", "faction", "parent", "headquarters"]) if (n[k] && ids.has(n[k])) structural.push([n["@id"], n[k]]);
    const pos = layout(nodes, [...structural, ...snap.edges.filter((e) => e["@type"] === "RelationshipEdge" || e["@type"] === "ControlEdge").map((e) => [e.from_id, e.to_id ?? e.target_id] as [string, string])], prevPos.current, W, H);
    pos.forEach((p, id) => prevPos.current.set(id, { x: p.x, y: p.y }));
    return { nodes, edges, pos, structural };
  }, [snap, edgeKinds]);

  const sel = snap?.nodes.find((n) => n["@id"] === selected);
  const name = (id?: string) => (id ? snap?.names[id] ?? id : "—");

  return (
    <div className="space-y-5">
      <Card title="Time-Travel Inspector" subtitle="Valid time (in-world day) × transaction time (commit) on any branch.">
        <div className="grid gap-4 md:grid-cols-[1fr_2fr_1.4fr]">
          <Field label="Branch">
            <select className={inputCls} value={branch} onChange={(e) => { setBranch(e.target.value); setDay(null); setSelected(null); }}>
              {branches.map((b) => <option key={b.id} value={b.name}>{b.name}</option>)}
            </select>
          </Field>
          <Field label={`In-world day: ${day ?? "…"}`} hint="What was true on this day (in_world_start ≤ day < in_world_end)">
            <input type="range" min={0} max={maxDay} value={day ?? 0} onChange={(e) => setDay(+e.target.value)} className="w-full accent-amber-500" />
          </Field>
          <Field label="As recorded at commit" hint="Transaction time — what the DB believed after that commit">
            <select className={inputCls} value={commit} onChange={(e) => setCommit(e.target.value)}>
              <option value="">Latest (branch head)</option>
              {commits.map((c) => (
                <option key={c.commit} value={c.commit}>{c.commit.slice(0, 7)} · {c.message.slice(0, 60)}</option>
              ))}
            </select>
          </Field>
        </div>
        {snap && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {Object.entries(snap.counts).filter(([, v]) => v > 0).map(([k, v]) => <Badge key={k}>{k}: {v}</Badge>)}
            {loading && <Spinner />}
          </div>
        )}
        <div className="mt-2"><ErrorBox error={error} /></div>
      </Card>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Card
          title="World graph"
          actions={
            <div className="flex flex-wrap gap-2 text-xs">
              {["RelationshipEdge", "ControlEdge", "DesireEdge", "EpistemicEdge"].map((k) => (
                <label key={k} className="flex items-center gap-1 text-ink-300">
                  <input type="checkbox" className="accent-amber-500" checked={edgeKinds.has(k)} onChange={() => {
                    const s = new Set(edgeKinds); s.has(k) ? s.delete(k) : s.add(k); setEdgeKinds(s);
                  }} />
                  {k.replace("Edge", "")}
                </label>
              ))}
            </div>
          }
        >
          {!graph ? <Spinner /> : (
            <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full rounded-lg bg-ink-950">
              <defs>
                <marker id="arr" viewBox="0 0 10 10" refX="16" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill="#7d8796" />
                </marker>
              </defs>
              {graph.structural.map(([s, d], i) => {
                const a = graph.pos.get(s)!, b = graph.pos.get(d)!;
                return <line key={`s${i}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#2b313a" strokeDasharray="3 4" />;
              })}
              {graph.edges.map(({ e, s, d }) => {
                const a = graph.pos.get(s)!, b = graph.pos.get(d)!;
                const t = e["@type"];
                const color = t === "RelationshipEdge" ? (e.trust_score < 0 ? "#f43f5e" : "#10b981") : t === "ControlEdge" ? "#4fb3d9" : t === "DesireEdge" ? (e.kind === "FEARS" ? "#a78bfa" : "#f0ad4e") : "#5d6573";
                const active = selected && (s === selected || d === selected);
                const width = t === "RelationshipEdge" ? 0.6 + Math.abs(e.trust_score) / 40 : 1.2;
                const mx = (a.x + b.x) / 2 + (b.y - a.y) * 0.08, my = (a.y + b.y) / 2 - (b.x - a.x) * 0.08;
                return (
                  <path key={e["@id"]} d={`M${a.x},${a.y} Q${mx},${my} ${b.x},${b.y}`} fill="none" stroke={color}
                    strokeWidth={active ? width + 1 : width} opacity={selected ? (active ? 0.95 : 0.12) : 0.55} markerEnd="url(#arr)">
                    <title>{`${name(s)} → ${name(d)}: ${t === "RelationshipEdge" ? `${e.label} (${e.trust_score})` : e.kind}`}</title>
                  </path>
                );
              })}
              {graph.nodes.map((n) => {
                const p = graph.pos.get(n["@id"])!;
                const dead = !n.alive;
                const isSel = selected === n["@id"];
                return (
                  <g key={n["@id"]} transform={`translate(${p.x},${p.y})`} className="cursor-pointer" onClick={() => setSelected(isSel ? null : n["@id"])}>
                    <circle r={n["@type"] === "Faction" ? 11 : 8} fill={dead ? "#3a414c" : TYPE_COLOR[n["@type"]]} stroke={isSel ? "#fff" : "#0d0f12"} strokeWidth={isSel ? 2.5 : 1.5}
                      opacity={selected && !isSel ? 0.75 : 1} />
                    {n.is_party && <circle r={15} fill="none" stroke="#f0ad4e" strokeDasharray="2 3" />}
                    <text y={-14} textAnchor="middle" className="select-none" fontSize={11} fill={dead ? "#7d8796" : "#e6eaf0"} style={{ paintOrder: "stroke", stroke: "#0d0f12", strokeWidth: 3 }}>
                      {dead ? "☠ " : ""}{n.name}
                    </text>
                  </g>
                );
              })}
            </svg>
          )}
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-ink-400">
            {GRAPH_TYPES.map((t) => <span key={t} className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: TYPE_COLOR[t] }} />{t}</span>)}
            <span>· click a node to see its epistemic view</span>
          </div>
        </Card>

        <div className="space-y-5">
          {sel ? (
            <Card title={sel.name} subtitle={`${sel["@type"]}${sel.role ? ` · ${sel.role}` : ""}`} actions={<Badge tone={sel.alive ? "green" : "red"}>{sel.alive ? "active" : `ended day ${sel.in_world_end}`}</Badge>}>
              {sel.description && <p className="text-sm text-ink-300">{sel.description}</p>}
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
                {(sel.aliases ?? []).map((a: string) => <Badge key={a} tone="slate">{a}</Badge>)}
              </div>
              {agentView ? (
                <div className="mt-4 space-y-3 text-sm">
                  <Section title="Desires">
                    {agentView.desires.map((d: any, i: number) => <li key={i}><Badge tone={d.kind === "FEARS" ? "red" : d.kind === "NEEDS" ? "violet" : "blue"}>{d.kind}</Badge> {d.target_name !== "—" ? d.target_name : d.target_text} <span className="text-ink-400">p={d.priority}</span></li>)}
                  </Section>
                  <Section title="Goals">
                    {agentView.goals.map((g: any) => <li key={g.id}>{g.name} <span className="text-ink-400">({g.clock})</span></li>)}
                  </Section>
                  <Section title="Controls">
                    {agentView.controls.map((c: any, i: number) => <li key={i}>{c.kind} {c.target_name}</li>)}
                  </Section>
                  <Section title="Relationships">
                    {agentView.relationships.map((r: any, i: number) => <li key={i} className="flex items-center gap-2"><TrustBar value={r.trust} /> <span>{r.to_name}</span> <span className="text-ink-400">{r.label} ({r.trust})</span></li>)}
                  </Section>
                  <Section title={`Knows / believes (${agentView.known_world.length}) — the only things its prompt can see`}>
                    {agentView.known_world.map((k: any) => (
                      <li key={k.id}>
                        <span className={cx(k.epistemic === "BELIEVES" ? "text-ember-300" : "text-ink-200")}>{k.name}</span>{" "}
                        <span className="text-ink-400">{k.type}{k.epistemic ? ` · ${k.epistemic} ${Math.round((k.confidence ?? 1) * 100)}%` : " · own context"}</span>
                      </li>
                    ))}
                  </Section>
                </div>
              ) : (
                <pre className="scroll-thin mt-3 max-h-80 overflow-auto rounded bg-ink-950 p-2 text-xs text-ink-300">{JSON.stringify(sel, null, 2)}</pre>
              )}
            </Card>
          ) : (
            <Card title="Relationships on this day">
              {!snap ? <Spinner /> : (
                <ul className="scroll-thin max-h-[560px] space-y-1.5 overflow-auto text-sm">
                  {snap.edges.filter((e) => e["@type"] === "RelationshipEdge").sort((a, b) => a.trust_score - b.trust_score).map((e) => (
                    <li key={e["@id"]} className="flex items-center gap-2">
                      <TrustBar value={e.trust_score} />
                      <span className="w-8 text-right font-mono text-xs text-ink-400">{e.trust_score}</span>
                      <span className="truncate text-ink-200">{name(e.from_id)} → {name(e.to_id)}</span>
                      <span className="text-xs text-ink-400">{e.label}</span>
                    </li>
                  ))}
                  {!snap.edges.some((e) => e["@type"] === "RelationshipEdge") && <Empty>No relationships active.</Empty>}
                </ul>
              )}
            </Card>
          )}
          {snap && (
            <Card title="Events up to this day">
              <ul className="scroll-thin max-h-72 space-y-1 overflow-auto text-sm">
                {snap.nodes.filter((n) => n["@type"] === "Event").sort((a, b) => (b.in_world_start ?? 0) - (a.in_world_start ?? 0)).map((e) => (
                  <li key={e["@id"]} className="flex gap-2"><span className="w-10 shrink-0 font-mono text-xs text-ink-400">d{e.in_world_start}</span><span className="text-ink-200">{e.name}</span></li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-400">{title}</div>
      <ul className="space-y-1 text-ink-200">{children}</ul>
    </div>
  );
}
