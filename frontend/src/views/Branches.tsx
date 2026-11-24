import { useEffect, useState } from "react";
import { api, Branch, Commit } from "../api";
import { Badge, Button, Card, ErrorBox, Spinner } from "../ui";

export default function Branches({ branches, onChanged, onOpenBriefing }: {
  branches: Branch[];
  onChanged: () => void;
  onOpenBriefing: (b: string) => void;
}) {
  const [open, setOpen] = useState<string | null>("main");
  const [log, setLog] = useState<Commit[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLog(null);
    api.log(open, 60).then(setLog).catch((e) => setError(e.message));
  }, [open, branches]);

  const act = async (key: string, fn: () => Promise<any>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <Card title="Branches" subtitle="Canon lives on main; every forecast is a disposable branch.">
        <ErrorBox error={error} />
        <ul className="space-y-2">
          {branches.map((b) => (
            <li key={b.id} className={`rounded-lg border px-3 py-2 ${open === b.name ? "border-ember-500/60 bg-ember-500/5" : "border-ink-800"}`}>
              <button className="w-full text-left" onClick={() => setOpen(b.name)}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-ink-100">{b.name}</span>
                  {b.id === "main" && <Badge tone="ember">canon</Badge>}
                  {b.live?.status === "running" && <Badge tone="ember"><Spinner className="h-3 w-3" /> running</Badge>}
                  {b.run?.mock_llm && <Badge tone="violet">mock</Badge>}
                </div>
                <div className="mt-0.5 text-xs text-ink-400">
                  head <span className="font-mono">{b.head?.slice(0, 8)}</span>
                  {b.run && <> · session {b.run.session_number} · days {b.run.start_day}–{b.run.end_day}</>}
                </div>
              </button>
              {b.is_forecast && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button onClick={() => onOpenBriefing(b.name)}>Briefing</Button>
                  <Button variant="primary" disabled={!!busy} onClick={() => confirm(`Merge ${b.name} into main?`) && act(`m${b.id}`, () => api.merge(b.name))}>
                    {busy === `m${b.id}` ? <Spinner /> : "Merge"}
                  </Button>
                  <Button variant="danger" disabled={!!busy} onClick={() => confirm(`Delete ${b.name}?`) && act(`d${b.id}`, () => api.discard(b.name))}>Discard</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
        <div className="mt-4 border-t border-ink-800 pt-3">
          <Button variant="danger" disabled={!!busy} onClick={() => confirm("Reset the database to the Greywater demo world? ALL branches will be deleted.") && act("seed", api.seed)}>
            {busy === "seed" ? <Spinner /> : "Reset demo world"}
          </Button>
        </div>
      </Card>

      <Card title="Commit history" subtitle={open ? <span className="font-mono">{open}</span> : undefined}>
        {!log ? <Spinner /> : (
          <ol className="relative space-y-3 border-l border-ink-700 pl-4">
            {log.map((c) => (
              <li key={c.commit}>
                <span className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full bg-ink-600" />
                <div className="text-sm text-ink-100">{c.message}</div>
                <div className="text-xs text-ink-400"><span className="font-mono">{c.commit.slice(0, 10)}</span> · {c.author} · {new Date(c.timestamp * 1000).toLocaleString()}</div>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}
