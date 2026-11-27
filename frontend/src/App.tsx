import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { api, Branch, Campaign, Health } from "./api";
import { Badge, cx } from "./ui";
import Branches from "./views/Branches";
import Briefing from "./views/Briefing";
import Forecaster from "./views/Forecaster";
import Homebrew from "./views/Homebrew";
import Ingest from "./views/Ingest";
import NewWorld from "./views/NewWorld";
import TimeTravel from "./views/TimeTravel";

// MapLibre is large: load the atlas only when its tab opens
const Atlas = lazy(() => import("./views/Atlas"));

type View = "forecast" | "briefing" | "atlas" | "branches" | "time" | "ingest" | "homebrew" | "new";
const NAV: { id: View; label: string }[] = [
  { id: "forecast", label: "Session Forecaster" },
  { id: "briefing", label: "DM Briefing" },
  { id: "atlas", label: "World Atlas" },
  { id: "branches", label: "Branches" },
  { id: "time", label: "Time Travel" },
  { id: "ingest", label: "Ingest" },
  { id: "homebrew", label: "Homebrew" },
  { id: "new", label: "✦ New World" },
];

function initialView(): View {
  const h = window.location.hash.replace("#", "") as View;
  return NAV.some((n) => n.id === h) ? h : "forecast";
}

export default function App() {
  const [view, setView] = useState<View>(initialView);
  const [health, setHealth] = useState<Health | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);

  const refresh = useCallback(() => {
    api.branches().then(setBranches).catch(() => undefined);
  }, []);
  const refreshAll = useCallback(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.campaigns().then(setCampaigns).catch(() => undefined);
    refresh();
  }, [refresh]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  const switchCampaign = async (id: string) => {
    await api.activateCampaign(id);
    setSelected(null);
    refreshAll();
  };

  // Poll while any forecast is running so lists stay live.
  useEffect(() => {
    if (!branches.some((b) => b.live?.status === "running" || b.live?.status === "queued")) return;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [branches, refresh]);

  const go = (v: View) => {
    setView(v);
    window.location.hash = v;
  };
  const openBriefing = (b: string) => {
    setSelected(b);
    refresh();
    go("briefing");
  };

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-10 border-b border-ink-800 bg-ink-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <span className="text-xl">🕯️</span>
            <div>
              <div className="font-display text-lg leading-tight text-ink-100">Living World Engine</div>
              <div className="text-[11px] uppercase tracking-[0.18em] text-ink-400">DM Forecast Dashboard</div>
            </div>
          </div>
          <nav className="flex flex-1 flex-wrap gap-1">
            {NAV.map((n) => (
              <button
                key={n.id}
                onClick={() => go(n.id)}
                className={cx(
                  "rounded-lg px-3 py-1.5 text-sm transition",
                  view === n.id ? "bg-ink-800 text-ember-300 ring-1 ring-ink-600" : "text-ink-300 hover:bg-ink-900 hover:text-ink-100",
                )}
              >
                {n.label}
              </button>
            ))}
          </nav>
          <div className="flex items-center gap-2 text-xs">
            {campaigns.length > 0 && health && (
              <select
                title="Campaign (each world is its own database)"
                className="rounded-md border border-ink-600 bg-ink-900 px-2 py-1 text-xs text-ink-100 focus:border-ember-500 focus:outline-none"
                value={health.campaign}
                onChange={(e) => switchCampaign(e.target.value)}
              >
                {campaigns.map((c) => <option key={c.id} value={c.id}>{c.label === "Living World" ? `Greywater demo${c.id !== health.default_campaign ? ` (${c.id})` : ""}` : c.label}</option>)}
              </select>
            )}
            {health ? (
              <>
                <Badge tone={health.terminusdb.ok ? "green" : "red"} title={health.terminusdb.error}>
                  TerminusDB {health.terminusdb.ok ? health.terminusdb.version : "down"}
                </Badge>
                <Badge tone={health.mock_llm ? "violet" : "blue"}>{health.mock_llm ? "MOCK_LLM" : health.llm_model}</Badge>
              </>
            ) : (
              <Badge tone="red">backend unreachable</Badge>
            )}
          </div>
        </div>
      </header>
      {/* remount every view when the campaign changes so nothing shows the previous world */}
      <main key={health?.campaign ?? "none"} className="mx-auto max-w-[1500px] px-5 py-6">
        {view === "forecast" && <Forecaster health={health} onOpenBriefing={openBriefing} onChanged={refresh} />}
        {view === "briefing" && <Briefing branches={branches} selected={selected} onSelect={setSelected} onChanged={refresh} />}
        {view === "atlas" && (
          <Suspense fallback={<div className="text-sm text-ink-400">Loading the atlas…</div>}>
            <Atlas branches={branches} />
          </Suspense>
        )}
        {view === "branches" && <Branches branches={branches} onChanged={refresh} onOpenBriefing={openBriefing} />}
        {view === "time" && <TimeTravel branches={branches} />}
        {view === "ingest" && <Ingest branches={branches} health={health} onChanged={refresh} />}
        {view === "homebrew" && <Homebrew />}
        {view === "new" && <NewWorld onSwitched={() => { refreshAll(); go("atlas"); }} />}
      </main>
    </div>
  );
}
