import { useEffect, useMemo, useState } from "react";
import { api, WizardAnswers, WizardField, WizardJob, WizardSpec } from "../api";
import { Badge, Button, Card, cx, ErrorBox, Field, inputCls, Spinner } from "../ui";

const STEP_GUIDE_ORDER = ["world", "continents", "kingdoms", "local", "history"];

export default function NewWorld({ onSwitched }: { onSwitched: () => void }) {
  const [spec, setSpec] = useState<WizardSpec | null>(null);
  const [answers, setAnswers] = useState<WizardAnswers | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<WizardJob | null>(null);
  const [upload, setUpload] = useState<{ name: string; states: number } | null>(null);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    api.wizardSpec()
      .then((s) => {
        setSpec(s);
        setAnswers(structuredClone(s.focuses["classic-realms"].defaults));
      })
      .catch((e) => setError(e.message));
  }, []);

  // poll a running build
  useEffect(() => {
    if (!job || job.status === "complete" || job.status === "failed") return;
    const t = setTimeout(() => api.wizardJob(job.id).then(setJob).catch(() => undefined), 600);
    return () => clearTimeout(t);
  }, [job]);

  const order = useMemo(
    () => (spec && answers ? spec.approaches[answers.approach]?.order ?? STEP_GUIDE_ORDER : STEP_GUIDE_ORDER),
    [spec, answers],
  );

  if (!spec || !answers) return error ? <ErrorBox error={error} /> : <Spinner />;

  const set = (step: string, key: string, value: any) =>
    setAnswers((a) => (a ? { ...a, [step]: { ...a[step], [key]: value } } : a));

  const chooseFocus = (focus: string) => {
    const d = structuredClone(spec.focuses[focus].defaults);
    // keep what the DM already typed
    d.approach = answers.approach;
    d.world.name = answers.world.name;
    d.world.seed = answers.world.seed;
    d.world.lore = answers.world.lore;
    d.local.town_name = answers.local.town_name;
    d.local.party_name = answers.local.party_name;
    setAnswers(d);
  };

  const rollField = (step: string, f: WizardField) => {
    const pick = <T,>(xs: T[]) => xs[Math.floor(Math.random() * xs.length)];
    if (f.roll && spec.rolls[f.roll]) return set(step, f.key, pick(spec.rolls[f.roll]));
    if (f.type === "select" && f.options) return set(step, f.key, pick(Object.keys(f.options)));
    if (f.type === "number") {
      if (f.key === "seed") return set(step, f.key, Math.floor(Math.random() * 1e9));
      const lo = f.min ?? 1, hi = f.max ?? 10;
      return set(step, f.key, lo + Math.floor(Math.random() * (hi - lo + 1)));
    }
    if (f.type === "checkbox") return set(step, f.key, Math.random() < 0.5);
  };
  const rollStep = (step: string) =>
    spec.steps[step].fields
      .filter((f) => !(step === "world" && f.key === "source") && f.type !== "checkbox" || f.key === "saga")
      .forEach((f) => rollField(step, f));

  const build = async () => {
    setError(null);
    try {
      setJob(await api.wizardBuild(answers));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const doUpload = async (file: File) => {
    setError(null);
    try {
      const r = await api.wizardUpload(file);
      setUpload({ name: r.name, states: r.states });
      set("world", "upload_id", r.upload_id);
      if (!answers.world.name) set("world", "name", r.name);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const switchTo = async () => {
    if (!job?.result) return;
    setSwitching(true);
    try {
      await api.activateCampaign(job.result.campaign);
      onSwitched();
    } catch (e) {
      setError((e as Error).message);
      setSwitching(false);
    }
  };

  const building = job && job.status !== "complete" && job.status !== "failed";

  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(320px,1fr)]">
      <div className="space-y-5">
        <Card title="Create a new world" subtitle="Follows the decision order of the AD&D World Builder's Guidebook. Each world gets its own campaign, so nothing here touches your current one.">
          <div className="grid gap-3 md:grid-cols-4">
            {Object.entries(spec.approaches).map(([id, ap]) => (
              <button
                key={id}
                onClick={() => setAnswers({ ...answers, approach: id })}
                className={cx("rounded-lg border p-3 text-left transition", answers.approach === id ? "border-ember-500 bg-ember-500/10" : "border-ink-700 hover:border-ink-600")}
              >
                <div className="text-sm font-medium text-ink-100">{ap.label}</div>
                <div className="mt-1 text-xs text-ink-400">{ap.blurb}</div>
              </button>
            ))}
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_2fr]">
            <Field label="Campaign focus" hint="Sets sensible defaults for every step">
              <select className={inputCls} value={answers.focus} onChange={(e) => chooseFocus(e.target.value)}>
                {Object.entries(spec.focuses).map(([id, f]) => <option key={id} value={id}>{f.label}</option>)}
              </select>
            </Field>
            <p className="self-end pb-2 text-xs text-ink-400">
              Your approach sets the order you answer in. The world itself is always built planet-first (a town needs a planet to sit on),
              one <span className="font-mono">[setup]</span> commit per step, so you can diff or rebuild it like any branch.
            </p>
          </div>
        </Card>

        {order.map((stepId, i) => {
          const step = spec.steps[stepId];
          const vals = answers[stepId] ?? {};
          return (
            <Card
              key={stepId}
              title={<><span className="mr-2 text-ember-300">{i + 1}.</span>{step.title}</>}
              subtitle={`Guidebook: ${step.guide}`}
              actions={<Button variant="ghost" onClick={() => rollStep(stepId)} title="Roll random choices for this step">🎲 Surprise me</Button>}
            >
              <div className="grid gap-3 md:grid-cols-3">
                {step.fields.map((f) => {
                  if (stepId === "world" && f.key === "lore") return null;
                  return (
                    <Field key={f.key} label={f.label} hint={f.hint}>
                      <div className="flex gap-1.5">
                        {f.type === "select" ? (
                          <select className={inputCls} value={vals[f.key] ?? ""} onChange={(e) => set(stepId, f.key, e.target.value)}>
                            {Object.entries(f.options ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                          </select>
                        ) : f.type === "checkbox" ? (
                          <label className="flex h-9 items-center gap-2 text-sm text-ink-200">
                            <input type="checkbox" className="accent-amber-500" checked={!!vals[f.key]} onChange={(e) => set(stepId, f.key, e.target.checked)} />
                            include
                          </label>
                        ) : (
                          <input
                            className={inputCls}
                            type={f.type === "number" ? "number" : "text"}
                            min={f.min}
                            max={f.max}
                            value={vals[f.key] ?? ""}
                            placeholder={f.key === "seed" ? "random" : ""}
                            onChange={(e) => set(stepId, f.key, f.type === "number" ? (e.target.value === "" ? null : +e.target.value) : e.target.value)}
                          />
                        )}
                        {(f.roll || f.type === "number" || f.type === "select") && (
                          <button className="rounded-md px-1.5 text-ink-400 hover:bg-ink-800 hover:text-ink-100" title="Roll" onClick={() => rollField(stepId, f)}>🎲</button>
                        )}
                      </div>
                    </Field>
                  );
                })}
              </div>
              {stepId === "world" && (
                <div className="mt-3 space-y-3">
                  <Field label="One-line pitch" hint="Recorded in the World commit">
                    <input className={inputCls} value={vals.lore ?? ""} placeholder="A drowned empire's heirs squabble over its islands…" onChange={(e) => set("world", "lore", e.target.value)} />
                  </Field>
                  {vals.source === "azgaar" && (
                    <div className="rounded-lg border border-dashed border-ink-600 p-3 text-sm text-ink-300">
                      Upload the file from Azgaar's <b>Export → Export to JSON → Full</b>.{" "}
                      <label className="cursor-pointer text-ember-300 hover:text-ember-400">
                        Choose file
                        <input type="file" accept=".json" className="hidden" onChange={(e) => e.target.files?.[0] && doUpload(e.target.files[0])} />
                      </label>
                      {upload && <span className="ml-2"><Badge tone="green">{upload.name} · {upload.states} realms</Badge></span>}
                      <p className="mt-1 text-xs text-ink-400">Land, climate, realm and culture counts come from your map; the other steps still apply.</p>
                    </div>
                  )}
                </div>
              )}
            </Card>
          );
        })}
      </div>

      <div className="space-y-5 xl:sticky xl:top-20 xl:self-start">
        <Card title="Your world">
          <dl className="space-y-1.5 text-sm">
            <Row k="Name" v={answers.world.name || "rolled at build time"} />
            <Row k="Approach" v={spec.approaches[answers.approach].label} />
            <Row k="Focus" v={spec.focuses[answers.focus].label} />
            <Row k="Land" v={answers.world.source === "azgaar" ? (upload ? `Azgaar: ${upload.name}` : "Azgaar export (not uploaded)") : `${answers.continents.land}, ${answers.world.climate}`} />
            <Row k="Realms" v={`${answers.kingdoms.n_states} · ${answers.kingdoms.hostility}`} />
            <Row k="Peoples" v={`${answers.kingdoms.n_cultures} cultures · ${answers.kingdoms.species_mix}`} />
            <Row k="Faiths" v={String(answers.kingdoms.n_religions)} />
            <Row k="Start" v={`${answers.local.town_name || "a port town"} · ${answers.local.local_factions}`} />
            <Row k="History" v={`${answers.history.years_back} years · ${answers.history.cataclysm}`} />
            <Row k="Monsters" v={`${answers.history.monster_density} · levels ${answers.history.level_band}`} />
          </dl>
          <div className="mt-4">
            <Button variant="primary" className="w-full" onClick={build} disabled={!!building || (answers.world.source === "azgaar" && !answers.world.upload_id)}>
              {building ? <Spinner className="border-t-ink-950" /> : "✦"} Build this world
            </Button>
          </div>
          <div className="mt-2"><ErrorBox error={error} /></div>
          <p className="mt-3 text-[11px] leading-snug text-ink-400">
            Creatures, peoples, classes and magic items come from the System Reference Document 5.2.1 ("SRD 5.2.1") by
            Wizards of the Coast LLC, available at dndbeyond.com/srd, licensed under CC-BY-4.0.
          </p>
        </Card>

        {job && (
          <Card title="Building" subtitle={job.result ? <span className="font-mono text-xs">{job.result.campaign}</span> : undefined}>
            <ol className="space-y-2 text-sm">
              {STEP_GUIDE_ORDER.map((s, i) => {
                const done = job.events.find((e) => e.step === s && e.kind === "done");
                const started = job.events.find((e) => e.step === s && e.kind === "start");
                return (
                  <li key={s} className="flex gap-2">
                    <span className={cx("mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] ring-1",
                      done ? "bg-emerald-500/20 text-emerald-300 ring-emerald-500/50" : started ? "animate-pulse bg-ember-500/20 text-ember-300 ring-ember-400" : "bg-ink-800 text-ink-400 ring-ink-600")}>
                      {done ? "✓" : i + 1}
                    </span>
                    <span className={done ? "text-ink-200" : "text-ink-400"}>{done ? done.message : started ? started.message + "…" : spec.steps[s].title}</span>
                  </li>
                );
              })}
            </ol>
            <ErrorBox error={job.error} />
            {job.result && (
              <div className="mt-4 space-y-2">
                <Button variant="primary" className="w-full" disabled={switching} onClick={switchTo}>
                  {switching ? <Spinner /> : "→"} Switch to {job.result.name} and open the atlas
                </Button>
                <p className="text-xs text-ink-400">Your current campaign stays as it is; switch back any time from the campaign menu at the top.</p>
              </div>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-3">
      <dt className="w-20 shrink-0 text-ink-400">{k}</dt>
      <dd className="text-ink-100">{v}</dd>
    </div>
  );
}
