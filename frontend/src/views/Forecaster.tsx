import { useEffect, useMemo, useRef, useState } from "react";
import { api, Health, ProgressEvent, RunSummary } from "../api";
import { Badge, Button, Card, cx, ErrorBox, Field, inputCls, Spinner } from "../ui";

const STEPS = [
  { id: "fork_canon", label: "Fork canon", desc: "Branch main → forecast/*" },
  { id: "apply_session_delta", label: "Apply session", desc: "Party actions → events, trust, knowledge" },
  { id: "select_active_agents", label: "Activation radius", desc: "2-hop neighbours + ticking goal clocks", loop: true },
  { id: "run_agent_turns", label: "Agent turns (BDI)", desc: "Each agent reasons over only what it KNOWS", loop: true },
  { id: "world_arbiter", label: "World arbiter", desc: "Resolve clashes → WorldEvents + shifts", loop: true },
  { id: "propagate_rumors", label: "Rumors", desc: "Public / leaked events → tavern talk", loop: true },
  { id: "finalize", label: "Finalize", desc: "Write forecast report" },
];

export default function Forecaster({ health, onOpenBriefing, onChanged }: {
  health: Health | null;
  onOpenBriefing: (branch: string) => void;
  onChanged: () => void;
}) {
  const [session, setSession] = useState(14);
  const [startDay, setStartDay] = useState(10);
  const [endDay, setEndDay] = useState(11);
  const [forecastDays, setForecastDays] = useState(7);
  const [summary, setSummary] = useState("");
  const [demo, setDemo] = useState("");
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [run, setRun] = useState<{ id: string; branch: string } | null>(null);
  const [status, setStatus] = useState<"idle" | "running" | "complete" | "failed">("idle");
  const [error, setError] = useState<string | null>(null);
  const [busyFile, setBusyFile] = useState(false);
  const [stalled, setStalled] = useState<RunSummary[]>([]);
  const esRef = useRef<EventSource | null>(null);

  const loadStalled = () =>
    api.forecasts().then((rs) => setStalled(rs.filter((r) => r.resumable || r.running_elsewhere))).catch(() => undefined);
  useEffect(() => { loadStalled(); }, []);

  /** Follow a run's progress events (from the start: a resumed run replays its earlier steps first). */
  const follow = (runId: string, branch: string) => {
    esRef.current?.close();
    setEvents([]);
    setRun({ id: runId, branch });
    setStatus("running");
    const es = new EventSource(`/api/forecasts/${runId}/events`);
    esRef.current = es;
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as ProgressEvent;
      setEvents((prev) => [...prev, ev]);
      if (ev.kind === "error") setError(ev.message);
    };
    es.addEventListener("end", (m) => {
      const d = JSON.parse((m as MessageEvent).data);
      setStatus(d.status === "complete" ? "complete" : "failed");
      es.close();
      onChanged();
      loadStalled();
    });
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) return;
    };
  };

  const resume = async (r: RunSummary) => {
    setError(null);
    try {
      await api.resumeForecast(r.id);
      setStalled((s) => s.filter((x) => x.id !== r.id));
      follow(r.id, r.branch);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  useEffect(() => {
    api.meta().then((m) => {
      setSession(m.last_session + 1);
      setStartDay(m.current_day);
      setEndDay(m.current_day + 1);
      setDemo(m.demo_summary);
    }).catch(() => undefined);
    return () => esRef.current?.close();
  }, []);

  const start = async () => {
    setError(null);
    setEvents([]);
    try {
      const r = await api.forecast({
        session_number: session, start_day: startDay, end_day: endDay, forecast_days: forecastDays,
        session_summary: summary, base_branch: "main",
      });
      onChanged();
      follow(r.run_id, r.branch);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };

  const loadFile = async (f: File) => {
    setBusyFile(true);
    setError(null);
    try {
      if (/\.(mp3|wav|m4a|ogg|flac|webm|mp4)$/i.test(f.name)) {
        const r = await api.transcribe(f);
        setSummary(r.text);
      } else {
        setSummary(await f.text());
      }
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusyFile(false);
    }
  };

  const stepState = useMemo(() => {
    const st: Record<string, { state: "pending" | "active" | "done"; message?: string; round?: number }> = {};
    for (const s of STEPS) st[s.id] = { state: "pending" };
    for (const e of events) {
      if (!st[e.step]) continue;
      if (e.kind === "start") st[e.step] = { state: "active", round: e.data?.round };
      if (e.kind === "done") st[e.step] = { state: "done", message: e.message, round: e.data?.round };
    }
    return st;
  }, [events]);
  const round = Math.max(0, ...events.map((e) => (e.data?.round ?? 0) as number)) + 1;
  const done = events.filter((e) => e.kind === "done");

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      {stalled.length > 0 && (
        <div className="lg:col-span-2">
          <Card title="Unfinished forecasts" subtitle="Interrupted by a restart, or stopped by an error: Resume picks up after the last finished step. A forecast another process is still running can only be watched.">
            <ul className="space-y-2 text-sm">
              {stalled.map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-3">
                  <Badge tone={r.running_elsewhere ? "blue" : r.status === "interrupted" ? "ember" : "red"}>
                    {r.running_elsewhere ? "running elsewhere" : r.status}
                  </Badge>
                  <span className="text-ink-100">Session {r.request.session_number}</span>
                  <span className="text-xs text-ink-400">
                    days {r.request.end_day}–{r.request.end_day + r.request.forecast_days}
                    {r.running_elsewhere ? ` · in ${r.owner}` : ` · next step ${r.next_step}`}
                    {r.error ? ` · ${r.error}` : ""}
                  </span>
                  {r.running_elsewhere ? (
                    <Button className="ml-auto" variant="ghost" disabled={status === "running"} onClick={() => follow(r.id, r.branch)}
                      title="Follow its progress; it keeps running in its own process">
                      Watch
                    </Button>
                  ) : (
                    <Button className="ml-auto" disabled={status === "running"} onClick={() => resume(r)}>
                      ▶ Resume
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
      <Card
        title="Session Forecaster"
        subtitle="Tell the engine what the party did; it forks canon and lets the world move while they rest."
      >
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Session #">
            <input type="number" className={inputCls} value={session} min={0} onChange={(e) => setSession(+e.target.value)} />
          </Field>
          <Field label="Start day">
            <input type="number" className={inputCls} value={startDay} min={0} onChange={(e) => setStartDay(+e.target.value)} />
          </Field>
          <Field label="End day">
            <input type="number" className={inputCls} value={endDay} min={startDay} onChange={(e) => setEndDay(+e.target.value)} />
          </Field>
          <Field label="Days until next">
            <input type="number" className={inputCls} value={forecastDays} min={1} max={90} onChange={(e) => setForecastDays(+e.target.value)} />
          </Field>
        </div>
        <p className="mt-2 text-xs text-ink-400">
          In-world campaign days. Party actions are dated within {startDay}–{endDay}; the world is then simulated off-screen
          through day {endDay + forecastDays}.
        </p>

        <div className="mt-4">
          <div className="mb-1 flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-ink-400">Session summary</span>
            <div className="flex items-center gap-2">
              {busyFile && <Spinner />}
              <label className="cursor-pointer text-xs text-ember-300 hover:text-ember-400">
                Upload notes{health?.whisper ? " / audio" : ""}
                <input
                  type="file"
                  className="hidden"
                  accept={health?.whisper ? ".md,.txt,.markdown,audio/*" : ".md,.txt,.markdown"}
                  onChange={(e) => e.target.files?.[0] && loadFile(e.target.files[0])}
                />
              </label>
              <button
                className="text-xs text-ember-300 hover:text-ember-400"
                title="Pull the VTT's session log (DM notes and places visited) into this summary"
                onClick={async () => {
                  setError(null);
                  try {
                    const log = await api.tableLog();
                    if (!log.summary) { setError("The VTT's session log is empty."); return; }
                    setSummary((s) => (s.trim() ? `${s.trim()}\n\n${log.summary}` : log.summary));
                    if (log.start_day !== null) setStartDay(log.start_day);
                    if (log.end_day !== null) setEndDay(Math.max(log.end_day, log.start_day ?? log.end_day));
                  } catch (e) {
                    setError(`Couldn't read the table's log: ${(e as Error).message}`);
                  }
                }}
              >
                From the table
              </button>
              {demo && (
                <button className="text-xs text-ember-300 hover:text-ember-400" onClick={() => setSummary(demo)}>
                  Use demo summary
                </button>
              )}
            </div>
          </div>
          <textarea
            className={cx(inputCls, "h-52 resize-y font-mono text-[13px] leading-relaxed")}
            placeholder="The party stole the ledger from Lord Vane's study, bribed Captain Brand at the docks…"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button variant="primary" onClick={start} disabled={!summary.trim() || status === "running"}>
            {status === "running" ? <Spinner className="border-t-ink-950" /> : "▶"} Run Forecast
          </Button>
          {health && (
            <span className="text-xs text-ink-400">
              {health.mock_llm ? "Mock LLM — deterministic, no GPU needed" : `LLM: ${health.llm_model}`}
            </span>
          )}
        </div>
        <div className="mt-3">
          <ErrorBox error={error} />
        </div>
      </Card>

      <Card
        title="LangGraph progress"
        subtitle={run ? <span className="font-mono text-xs">{run.branch}</span> : "Run a forecast to watch each node execute."}
        actions={
          status === "complete" && run ? (
            <Button variant="primary" onClick={() => onOpenBriefing(run.branch)}>Open DM Briefing →</Button>
          ) : status === "running" ? (
            <Badge tone="ember"><Spinner className="h-3 w-3" /> round {round}</Badge>
          ) : status === "failed" ? (
            <Badge tone="red">failed</Badge>
          ) : null
        }
      >
        <ol className="relative space-y-1">
          {STEPS.map((s, i) => {
            const st = stepState[s.id];
            return (
              <li key={s.id} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <span
                    className={cx(
                      "mt-1 flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ring-1",
                      st.state === "done" && "bg-emerald-500/20 text-emerald-300 ring-emerald-500/50",
                      st.state === "active" && "bg-ember-500/20 text-ember-300 ring-ember-400 animate-pulse",
                      st.state === "pending" && "bg-ink-800 text-ink-400 ring-ink-600",
                    )}
                  >
                    {st.state === "done" ? "✓" : i + 1}
                  </span>
                  {i < STEPS.length - 1 && <span className="my-0.5 w-px flex-1 bg-ink-700" />}
                </div>
                <div className="min-w-0 flex-1 pb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-ink-100">{s.label}</span>
                    {s.loop && <Badge tone="slate">per round</Badge>}
                    {s.loop && st.round !== undefined && st.state !== "pending" && <Badge tone="ember">round {st.round + 1}</Badge>}
                  </div>
                  <div className="text-xs text-ink-400">{st.message ?? s.desc}</div>
                </div>
              </li>
            );
          })}
        </ol>
        {done.length > 0 && (
          <details className="mt-3 rounded-lg border border-ink-800 bg-ink-950/60" open={status !== "complete"}>
            <summary className="cursor-pointer px-3 py-2 text-xs uppercase tracking-wide text-ink-400">Execution log ({done.length})</summary>
            <ul className="scroll-thin max-h-72 space-y-2 overflow-auto px-3 pb-3 text-xs">
              {done.map((e) => (
                <li key={e.seq}>
                  <div className="text-ink-200"><span className="font-mono text-ember-300">{e.step}</span> — {e.message}</div>
                  {Array.isArray(e.data?.intents) && <ul className="ml-3 mt-1 list-disc pl-3 text-ink-400">{e.data.intents.map((x: string) => <li key={x}>{x}</li>)}</ul>}
                  {Array.isArray(e.data?.events) && <ul className="ml-3 mt-1 list-disc pl-3 text-ink-400">{e.data.events.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>}
                  {Array.isArray(e.data?.agents) && <div className="ml-3 mt-1 text-ink-400">{e.data.agents.join(" · ")}</div>}
                  {Array.isArray(e.data?.party_actions) && <ul className="ml-3 mt-1 list-disc pl-3 text-ink-400">{e.data.party_actions.map((x: string) => <li key={x}>{x}</li>)}</ul>}
                </li>
              ))}
            </ul>
          </details>
        )}
      </Card>
    </div>
  );
}
