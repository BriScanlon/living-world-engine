import { useCallback, useEffect, useMemo, useState } from "react";
import { api, Branch, Briefing as BriefingT, EventBrief } from "../api";
import {
  accuracyTone, Badge, Button, Card, Clock, cx, Empty, ErrorBox, inputCls, signed, Spinner, TrustBar, visibilityTone,
} from "../ui";

type Tab = "ripples" | "offscreen" | "shifts" | "rumors" | "intents" | "raw";

export default function Briefing({ branches, selected, onSelect, onChanged }: {
  branches: Branch[];
  selected: string | null;
  onSelect: (b: string | null) => void;
  onChanged: () => void;
}) {
  const forecasts = branches.filter((b) => b.is_forecast);
  const current = branches.find((b) => b.name === selected);
  const [data, setData] = useState<BriefingT | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("ripples");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);

  const running = current?.live?.status === "running" || current?.live?.status === "queued";

  const load = useCallback(async () => {
    if (!selected || running) return;
    setLoading(true);
    setError(null);
    try {
      setData(await api.diff("main", selected));
      setPicked(new Set());
    } catch (e) {
      setError((e as Error).message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [selected, running]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!selected && forecasts.length) onSelect(forecasts[forecasts.length - 1].name);
  }, [selected, forecasts, onSelect]);

  const act = async (label: string, fn: () => Promise<any>, after?: (r: any) => void) => {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      const r = await fn();
      after?.(r);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  if (!forecasts.length) {
    return <Empty>No forecast branches yet. Run one from the Session Forecaster.</Empty>;
  }

  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "ripples", label: "1 · Direct Ripple Effects", count: data?.ripples.length },
    { id: "offscreen", label: "2 · Off-Screen Events", count: data?.offscreen.length },
    { id: "shifts", label: "3 · Relationship & Motivation Shifts", count: data?.shifts.relationships.length },
    { id: "rumors", label: "4 · Tavern Rumors & Hooks", count: data?.rumors.length },
    { id: "intents", label: "Agent intents", count: data?.intents.length },
    { id: "raw", label: "Raw TerminusDB diff" },
  ];

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-end gap-4">
          <label className="min-w-72 flex-1">
            <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-ink-400">Compare <span className="font-mono normal-case">main</span> ⟶</span>
            <select className={inputCls} value={selected ?? ""} onChange={(e) => onSelect(e.target.value)}>
              {forecasts.map((b) => (
                <option key={b.id} value={b.name}>
                  {b.name}{b.run ? ` — session ${b.run.session_number}, days ${b.run.start_day}–${b.run.end_day}` : ""}{b.live?.status === "running" ? " (running…)" : ""}
                </option>
              ))}
            </select>
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={!!busy || running || !data}
              title="Merge the whole forecast into canon (3-way apply if main has moved)"
              onClick={() => confirm(`Merge ${selected} into main? This makes the forecast canon.`) &&
                act("merge", () => api.merge(selected!), (r) => setNotice(`Merged into main (${r.strategy}).`))}
            >
              {busy === "merge" ? <Spinner /> : "⤵"} Merge to Canon
            </Button>
            <Button
              disabled={!!busy || picked.size === 0}
              title="Copy only the selected events (and their consequences) into main"
              onClick={() => act("pick", () => api.cherryPick(selected!, [...picked]), (r) => {
                setNotice(`Cherry-picked ${picked.size} event(s) → ${r.count} documents written to main.`);
                load();
              })}
            >
              {busy === "pick" ? <Spinner /> : "🍒"} Cherry-Pick ({picked.size})
            </Button>
            <Button
              disabled={!!busy || running}
              onClick={() => confirm("Discard this forecast branch and run a fresh one with the same inputs?") &&
                act("rerun", () => api.rerun(selected!), (r) => {
                  setNotice(`Re-running as ${r.branch}…`);
                  setData(null);
                  onSelect(r.branch);
                })}
            >
              {busy === "rerun" ? <Spinner /> : "↻"} Discard & Re-run
            </Button>
            <Button
              variant="danger"
              disabled={!!busy}
              onClick={() => confirm(`Delete branch ${selected}? This cannot be undone.`) &&
                act("discard", () => api.discard(selected!), () => {
                  setNotice("Branch discarded.");
                  setData(null);
                  onSelect(null);
                })}
            >
              Discard
            </Button>
          </div>
        </div>
        {data?.run && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-ink-400">
            <Badge tone="ember">session {data.run.session_number}</Badge>
            <Badge>days {data.run.start_day}–{data.run.end_day}</Badge>
            <Badge>{data.rounds.length} rounds</Badge>
            {data.run.mock_llm && <Badge tone="violet">mock LLM</Badge>}
            <Badge tone="green">+{data.stats.added} docs</Badge>
            <Badge tone="blue">~{data.stats.changed} changed</Badge>
            <Badge>{data.stats.events} events</Badge>
            <Badge>{data.stats.rumors} rumors</Badge>
            {data.stats.violations > 0 && <Badge tone="red">{data.stats.violations} epistemic violations blocked</Badge>}
            <span className="font-mono">base {String(data.run.base_commit ?? "").slice(0, 8)}</span>
          </div>
        )}
        {(notice || error) && <div className="mt-3 space-y-2">{notice && <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">{notice}</div>}<ErrorBox error={error} /></div>}
      </Card>

      {running && (
        <Card><div className="flex items-center gap-3 text-sm text-ink-300"><Spinner /> Simulation running on this branch… the briefing will load when it finishes.</div></Card>
      )}
      {loading && <div className="flex items-center gap-2 text-sm text-ink-400"><Spinner /> Diffing branches…</div>}

      {data && !running && (
        <>
          <nav className="scroll-thin flex gap-1 overflow-x-auto border-b border-ink-800">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={cx(
                  "whitespace-nowrap border-b-2 px-3 py-2 text-sm transition",
                  tab === t.id ? "border-ember-400 text-ember-300" : "border-transparent text-ink-400 hover:text-ink-200",
                )}
              >
                {t.label}{t.count !== undefined && <span className="ml-1.5 rounded bg-ink-800 px-1.5 text-xs text-ink-300">{t.count}</span>}
              </button>
            ))}
          </nav>
          {tab === "ripples" && <Ripples data={data} />}
          {tab === "offscreen" && <Offscreen data={data} picked={picked} setPicked={setPicked} />}
          {tab === "shifts" && <Shifts data={data} />}
          {tab === "rumors" && <Rumors data={data} />}
          {tab === "intents" && <Intents data={data} />}
          {tab === "raw" && <RawDiff compare={data.compare} />}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------- 1. ripples
function EventLine({ ev, depth = 0 }: { ev: EventBrief; depth?: number }) {
  return (
    <div className={cx(depth > 0 && "ml-5 border-l border-ink-700 pl-4")}>
      <div className="relative py-2">
        {depth > 0 && <span className="absolute -left-4 top-4 h-px w-3 bg-ink-600" />}
        <div className="flex flex-wrap items-center gap-2">
          <Badge>day {ev.day}</Badge>
          <span className="text-sm font-medium text-ink-100">{ev.title}</span>
          <Badge tone={visibilityTone(ev.visibility)}>{ev.visibility}</Badge>
        </div>
        {ev.summary && <p className="mt-1 text-sm text-ink-300">{ev.summary}</p>}
        {ev.reasoning && <p className="mt-1 text-sm italic text-ink-400">“{ev.reasoning}”</p>}
      </div>
      {ev.reactions?.map((r) => <EventLine key={r.id} ev={r} depth={depth + 1} />)}
    </div>
  );
}

function Ripples({ data }: { data: BriefingT }) {
  if (!data.ripples.length) return <Empty>No party actions were recorded for this forecast.</Empty>;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      {data.ripples.map((r) => (
        <Card key={r.id} className="border-ember-600/40">
          <div className="text-xs font-medium uppercase tracking-wide text-ember-300">Party action · day {r.day}</div>
          <h3 className="mt-1 font-display text-lg text-ink-100">{r.title}</h3>
          <p className="mt-1 text-sm text-ink-300">{r.summary}</p>
          <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="text-ink-400">Affected →</span>
            {r.affected?.map((a) => <Badge key={a.id} tone="ember" title={a.type}>{a.name}</Badge>)}
          </div>
          {!!r.woken?.length && (
            <div className="mt-2 text-xs text-ink-400">
              Woke: {r.woken.map((w) => <span key={w.id} className="mr-2 text-ink-200" title={w.path.join(" → ")}>{w.name} <span className="text-ink-400">({w.distance} hop{w.distance === 1 ? "" : "s"})</span></span>)}
            </div>
          )}
          <div className="mt-3 border-t border-ink-800 pt-2">
            {r.reactions?.length ? r.reactions.map((x) => <EventLine key={x.id} ev={x} depth={1} />) : (
              <p className="text-sm text-ink-400">No direct NPC reaction yet — but word may still be spreading.</p>
            )}
          </div>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------- 2. off-screen
function Offscreen({ data, picked, setPicked }: { data: BriefingT; picked: Set<string>; setPicked: (s: Set<string>) => void }) {
  const [hideClocks, setHideClocks] = useState(true);
  const evs = data.offscreen.filter((e) => !(hideClocks && e.event_type === "goal_progress"));
  const byDay = useMemo(() => {
    const m = new Map<number, EventBrief[]>();
    for (const e of evs) m.set(e.day, [...(m.get(e.day) ?? []), e]);
    return [...m.entries()].sort((a, b) => a[0] - b[0]);
  }, [evs]);
  const toggle = (id: string) => {
    const s = new Set(picked);
    s.has(id) ? s.delete(id) : s.add(id);
    setPicked(s);
  };
  return (
    <Card
      title="Off-screen events & faction moves"
      subtitle="Tick events to cherry-pick them (with their consequences) into canon."
      actions={
        <label className="flex items-center gap-2 text-xs text-ink-300">
          <input type="checkbox" checked={hideClocks} onChange={(e) => setHideClocks(e.target.checked)} /> hide goal-clock ticks
        </label>
      }
    >
      {!evs.length && <Empty>Nothing happened off-screen.</Empty>}
      <div className="space-y-5">
        {byDay.map(([day, list]) => (
          <div key={day}>
            <div className="mb-2 font-display text-sm text-ember-300">Day {day}</div>
            <ul className="space-y-2">
              {list.map((e) => (
                <li key={e.id} className={cx("flex gap-3 rounded-lg border px-3 py-2", picked.has(e.id) ? "border-ember-500/60 bg-ember-500/5" : "border-ink-800 bg-ink-950/40")}>
                  <input type="checkbox" className="mt-1 accent-amber-500" checked={picked.has(e.id)} onChange={() => toggle(e.id)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-ink-100">{e.title}</span>
                      <Badge tone="slate">{e.event_type.replace("_", " ")}</Badge>
                      <Badge tone={visibilityTone(e.visibility)}>{e.visibility}</Badge>
                      {e.location && <span className="text-xs text-ink-400">@ {e.location.name}</span>}
                    </div>
                    <p className="mt-0.5 text-sm text-ink-300">{e.summary}</p>
                    {e.reasoning && <p className="mt-0.5 text-xs italic text-ink-400">“{e.reasoning}”</p>}
                    {e.caused_by && <p className="mt-0.5 text-xs text-ink-400">↳ in response to <span className="text-ink-200">{e.caused_by.title}</span></p>}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------- 3. shifts
function Shifts({ data }: { data: BriefingT }) {
  const s = data.shifts;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Relationships" subtitle="Trust −100 … +100, canon → forecast" className="xl:col-span-2">
        {!s.relationships.length ? <Empty>No relationship changes.</Empty> : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-ink-400">
                <tr><th className="py-1 pr-3">Who</th><th className="pr-3">Toward</th><th className="pr-3">Trust</th><th className="pr-3"></th><th className="pr-3">Label</th><th>Why</th></tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {s.relationships.map((r, i) => (
                  <tr key={i} className="align-top">
                    <td className="py-2 pr-3 text-ink-100">{r.from.name}</td>
                    <td className="pr-3 py-2 text-ink-200">{r.to.name}</td>
                    <td className="whitespace-nowrap pr-3 py-2 font-mono">
                      <span className="text-ink-400">{r.before ?? "—"}</span> → <span className={(r.after ?? 0) < 0 ? "text-rose-300" : "text-emerald-300"}>{r.after ?? "—"}</span>
                      <span className={cx("ml-2 text-xs", r.delta < 0 ? "text-rose-400" : "text-emerald-400")}>({signed(r.delta)})</span>
                    </td>
                    <td className="pr-3 py-2"><TrustBar value={r.after} ghost={r.before} /></td>
                    <td className="pr-3 py-2 text-xs text-ink-300">{r.label_before !== r.label_after ? <>{r.label_before ?? "—"} → <span className="text-ember-300">{r.label_after}</span></> : r.label_after}</td>
                    <td className="py-2 text-xs text-ink-400">{r.history.map((h, j) => <div key={j}>d{h.day}: {h.event_title}</div>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Goals & clocks">
        {!s.goals.length ? <Empty>No goal movement.</Empty> : (
          <ul className="space-y-3">
            {s.goals.map((g) => (
              <li key={g.id} className="flex items-center gap-3">
                <Clock filled={g.clock_after} segments={g.segments} prev={g.clock_before} size={34} />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium text-ink-100">{g.name}</span>
                    {g.is_new && <Badge tone="ember">new goal</Badge>}
                    {g.status_after !== "active" && <Badge tone="green">{g.status_after}</Badge>}
                  </div>
                  <div className="text-xs text-ink-400">
                    {g.owner.name} · clock {g.clock_before ?? 0} → {g.clock_after}/{g.segments}{g.target ? ` · target: ${g.target.name}` : ""}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Control & ownership">
        {!s.controls.length ? <Empty>No territory or items changed hands.</Empty> : (
          <ul className="space-y-1.5 text-sm">
            {s.controls.map((c, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2">
                <Badge>d{c.day}</Badge>
                <Badge tone={c.change === "gained" ? "green" : "red"}>{c.change}</Badge>
                <span className="text-ink-100">{c.holder}</span>
                <span className="text-ink-400">{c.kind.toLowerCase()}</span>
                <span className="text-ink-100">{c.target}</span>
                {c.event && <span className="text-xs text-ink-400">— {c.event}</span>}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Motivations (WANTS / NEEDS / FEARS)">
        {!s.desires.length ? <Empty>No new desires or fears.</Empty> : (
          <ul className="space-y-1.5 text-sm">
            {s.desires.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-2">
                <Badge tone={d.change === "added" ? "ember" : "slate"}>{d.change}</Badge>
                <span className="text-ink-100">{d.agent.name}</span>
                <Badge tone={d.kind === "FEARS" ? "red" : d.kind === "NEEDS" ? "violet" : "blue"}>{d.kind}</Badge>
                <span className="text-ink-100">{d.target}</span>
                <span className="text-xs text-ink-400">p={d.priority}</span>
                {d.note && <span className="text-xs italic text-ink-400">— {d.note}</span>}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Resources & casualties">
        {!s.resources.length && !s.deaths.length ? <Empty>No treasury changes or deaths.</Empty> : (
          <ul className="space-y-1.5 text-sm">
            {s.resources.map((r) => (
              <li key={r.faction} className="flex items-center gap-2">
                <span className="text-ink-100">{r.faction}</span>
                <span className="font-mono text-ink-400">{r.before} → {r.after}</span>
                <span className={cx("text-xs", r.after < r.before ? "text-rose-400" : "text-emerald-400")}>({signed(r.after - r.before)})</span>
              </li>
            ))}
            {s.deaths.map((d) => (
              <li key={d.id} className="text-rose-300">☠ {d.name} — day {d.day} ({d.event})</li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="What each agent learned" subtitle="New KNOWS / BELIEVES edges — this gates what they can act on next session." className="xl:col-span-2">
        <div className="grid gap-3 md:grid-cols-2">
          {s.knowledge.map((k) => (
            <details key={k.agent.id} className="rounded-lg border border-ink-800 bg-ink-950/40 px-3 py-2">
              <summary className="cursor-pointer text-sm text-ink-100">{k.agent.name} <span className="text-xs text-ink-400">({k.learned.length})</span></summary>
              <ul className="mt-2 space-y-1 text-xs text-ink-300">
                {k.learned.map((l, i) => (
                  <li key={i}><Badge tone={l.kind === "KNOWS" ? "green" : "ember"}>{l.kind} {Math.round(l.confidence * 100)}%</Badge> <span className="ml-1">{l.fact}</span> <span className="text-ink-400">(d{l.day})</span></li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------- 4. rumors
function Rumors({ data }: { data: BriefingT }) {
  const locations = [...new Map(data.rumors.map((r) => [r.location.id, r.location.name])).entries()];
  const [loc, setLoc] = useState("all");
  const [acc, setAcc] = useState<Set<string>>(new Set(["True", "Exaggerated", "False"]));
  const list = data.rumors.filter((r) => (loc === "all" || r.location.id === loc) && acc.has(r.accuracy));
  const toggle = (a: string) => {
    const s = new Set(acc);
    s.has(a) ? s.delete(a) : s.add(a);
    setAcc(s);
  };
  return (
    <Card
      title="Tavern rumors & player hooks"
      subtitle="Accuracy is for the DM's eyes only. Dialogue is ready to read aloud."
      actions={
        <>
          <select className={cx(inputCls, "w-auto py-1")} value={loc} onChange={(e) => setLoc(e.target.value)}>
            <option value="all">All locations</option>
            {locations.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
          {(["True", "Exaggerated", "False"] as const).map((a) => (
            <button key={a} onClick={() => toggle(a)} className={cx("rounded-md", !acc.has(a) && "opacity-35")}>
              <Badge tone={accuracyTone(a)}>{a}</Badge>
            </button>
          ))}
        </>
      }
    >
      {!list.length ? <Empty>No rumors match.</Empty> : (
        <div className="grid gap-3 md:grid-cols-2">
          {list.map((r) => (
            <figure key={r.id} className="rounded-lg border border-ink-800 bg-ink-950/50 p-4">
              <blockquote className="font-display text-[15px] leading-relaxed text-ink-100">{r.dialogue}</blockquote>
              <figcaption className="mt-3 flex flex-wrap items-center gap-2 text-xs text-ink-400">
                <span>— {r.speaker ?? "someone"}, {r.location.name}, day {r.day}</span>
                <Badge tone={accuracyTone(r.accuracy)}>{r.accuracy}</Badge>
                <button className="ml-auto text-ember-300 hover:text-ember-400" onClick={() => navigator.clipboard?.writeText(r.dialogue)}>copy</button>
              </figcaption>
              {r.about_event && <div className="mt-1 text-xs text-ink-400">Truth: {r.about_event.title}</div>}
            </figure>
          ))}
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------- extras
function Intents({ data }: { data: BriefingT }) {
  return (
    <Card title="Agent intents (BDI output)" subtitle="Every awakened agent's declared AgentIntent, per round, before arbitration.">
      {data.violations.length > 0 && (
        <div className="mb-3 rounded-lg border border-rose-500/30 bg-rose-500/10 p-2 text-xs text-rose-200">
          {data.violations.map((v, i) => <div key={i}>Round {v.round + 1}: {v.message}</div>)}
        </div>
      )}
      <div className="scroll-thin overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-ink-400">
            <tr><th className="py-1 pr-3">Rnd</th><th className="pr-3">Agent</th><th className="pr-3">Action</th><th className="pr-3">Target</th><th className="pr-3">Spend</th><th className="pr-3">Vis.</th><th>Reasoning</th></tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {data.intents.map((i, k) => (
              <tr key={k} className="align-top">
                <td className="py-2 pr-3 text-ink-400">{i.round + 1}</td>
                <td className="py-2 pr-3 text-ink-100">{i.agent_name}</td>
                <td className="py-2 pr-3"><Badge tone="ember">{i.action}</Badge></td>
                <td className="py-2 pr-3 text-ink-200">{i.target_name ?? "—"}</td>
                <td className="py-2 pr-3 font-mono text-ink-300">{i.resources_spent}</td>
                <td className="py-2 pr-3"><Badge tone={visibilityTone(i.public_or_secret)}>{i.public_or_secret}</Badge></td>
                <td className="py-2 text-xs italic text-ink-400">{i.reasoning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className="mt-5 mb-2 text-xs font-medium uppercase tracking-wide text-ink-400">Activation radius</h3>
      <ul className="space-y-1 text-xs text-ink-300">
        {data.activations.map((a, k) => (
          <li key={k}>
            <span className="text-ink-400">R{a.round + 1}</span> <span className="text-ink-100">{a.name}</span> —{" "}
            {a.path.length ? <>via {a.path.join(" → ")}</> : null}
            {Object.keys(a.goal_ticks).length ? <Badge tone="violet" className="ml-1">goal clock ticks</Badge> : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function RawDiff({ compare }: { compare: string }) {
  const [ops, setOps] = useState<any[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.rawDiff("main", compare).then(setOps).catch((e) => setError(e.message));
  }, [compare]);
  return (
    <Card title="TerminusDB native diff" subtitle="JSON patch produced by TerminusDB's /api/diff between main and this branch.">
      <ErrorBox error={error} />
      {!ops ? <Spinner /> : (
        <>
          <div className="mb-2 text-xs text-ink-400">{ops.length} document operations</div>
          <pre className="scroll-thin max-h-[60vh] overflow-auto rounded-lg bg-ink-950 p-3 text-xs text-ink-300">{JSON.stringify(ops, null, 2)}</pre>
        </>
      )}
    </Card>
  );
}
