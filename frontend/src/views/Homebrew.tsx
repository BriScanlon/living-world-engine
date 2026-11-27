import { useEffect, useMemo, useState } from "react";
import { api, HomebrewEntry, HomebrewKind, HomebrewState } from "../api";
import { Badge, Button, Card, cx, Empty, ErrorBox, Field, inputCls, Spinner } from "../ui";

const KIND_LABEL: Record<HomebrewKind, string> = { monster: "Creatures", species: "Peoples", item: "Treasures" };
const ONE: Record<HomebrewKind, string> = { monster: "creature", species: "people", item: "treasure" };
const BLANK: Record<HomebrewKind, HomebrewEntry> = {
  monster: { kind: "monster", name: "", size: "Medium", type: "Monstrosity", ac: 12, hp: 20, cr: "1", speed: "30 ft.", biomes: [] },
  species: { kind: "species", name: "", size: "Medium", speed: "30 feet", creature_type: "Humanoid" },
  item: { kind: "item", name: "", rarity: "Uncommon", category: "Wondrous Item", attunement: false },
};
const lines = (v: unknown) => (Array.isArray(v) ? v.join("\n") : (v as string) ?? "");

/**
 * The DM's own catalogue: creatures, peoples and treasures that live beside the SRD. New worlds built by the
 * wizard draw on them (creatures in the wilds of the biomes they list, peoples among the cultures and NPCs,
 * treasures as the contested item) and keep a copy.
 */
export default function Homebrew() {
  const [state, setState] = useState<HomebrewState | null>(null);
  const [draft, setDraft] = useState<HomebrewEntry | null>(null);
  const [editingUid, setEditingUid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [srdQ, setSrdQ] = useState("");
  const [srdHits, setSrdHits] = useState<{ id: string; name: string; kind: HomebrewKind; detail: string }[]>([]);

  const load = () => api.homebrew().then(setState).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (srdQ.trim().length < 2) { setSrdHits([]); return; }
    const t = setTimeout(() => api.searchSrd(srdQ, draft?.kind).then(setSrdHits).catch(() => undefined), 250);
    return () => clearTimeout(t);
  }, [srdQ, draft?.kind]);

  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true); setError(null); setNote(null);
    try { const m = await fn(); if (m) setNote(m); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const startNew = (kind: HomebrewKind) => { setDraft({ ...BLANK[kind] }); setEditingUid(null); setSrdQ(""); };
  const edit = (e: HomebrewEntry) => { setDraft({ ...e }); setEditingUid(e.uid ?? null); setSrdQ(""); };
  const set = (k: string, v: unknown) => setDraft((d) => (d ? { ...d, [k]: v } : d));

  const save = () => run(async () => {
    if (!draft) return;
    const saved = await api.saveHomebrew(draft, editingUid ?? undefined);
    setDraft(null); setEditingUid(null); await load();
    return `Saved ${saved.name} (${saved.id}).`;
  });
  const fromSrd = (id: string) => run(async () => {
    const d = await api.homebrewFromSrd(id);
    setDraft(d); setEditingUid(null); setSrdQ(""); setSrdHits([]);
    return "Started from the SRD entry: rename it and make it your own.";
  });
  const exportAll = () => {
    const a = document.createElement("a");
    a.href = "/api/homebrew/export";
    a.download = "homebrew.json";
    a.click();
  };
  const importFile = (f: File) => run(async () => {
    const items = JSON.parse(await f.text());
    const r = await api.importHomebrew(Array.isArray(items) ? items : [items]);
    await load();
    return `Imported ${r.imported}${r.rejected.length ? `; skipped ${r.rejected.map((x) => `${x.name ?? "#" + x.index}: ${x.error}`).join("; ")}` : ""}.`;
  });

  const groups = useMemo(() => {
    const g: Record<HomebrewKind, HomebrewEntry[]> = { monster: [], species: [], item: [] };
    for (const e of state?.entries ?? []) g[e.kind]?.push(e);
    return g;
  }, [state]);

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <Card title="Homebrew" subtitle="Your own creatures, peoples and treasures, beside the SRD. New worlds use them and keep a copy."
        actions={<div className="flex gap-2">
          <Button variant="ghost" onClick={exportAll} title="Download the library as JSON">Export</Button>
          <label className="cursor-pointer rounded-lg px-3 py-1.5 text-sm text-ember-300 hover:bg-ink-800">
            Import<input type="file" accept=".json,application/json" className="hidden"
              onChange={(e) => e.target.files?.[0] && importFile(e.target.files[0])} />
          </label>
        </div>}>
        {!state ? <Spinner /> : (
          <div className="space-y-4">
            {state.kinds.map((k) => (
              <div key={k}>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs font-medium uppercase tracking-wide text-ink-400">{KIND_LABEL[k]}</span>
                  <button className="text-xs text-ember-300 hover:text-ember-400" onClick={() => startNew(k)}>+ New {ONE[k]}</button>
                </div>
                {!groups[k].length ? <p className="text-sm text-ink-500">None yet.</p> : (
                  <ul className="space-y-1">
                    {groups[k].map((e) => (
                      <li key={e.id} className={cx("flex items-center gap-2 rounded-lg px-2 py-1 text-sm hover:bg-ink-800/60",
                        draft && editingUid === e.uid && "bg-ink-800")}>
                        <button className="min-w-0 flex-1 truncate text-left text-ink-100" onClick={() => edit(e)}>{e.name}</button>
                        <span className="text-xs text-ink-400">
                          {k === "monster" ? `CR ${e.cr} · AC ${e.ac} · HP ${e.hp}` : k === "item" ? String(e.rarity) : String(e.size ?? "")}
                        </span>
                        {e.overrides && <Badge tone="violet" title={`Replaces ${e.overrides}`}>replaces SRD</Badge>}
                        {e.in_world && <Badge tone="green" title={`This world (${state.campaign}) has a copy`}>in this world</Badge>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
            {!!state.in_world_only.length && (
              <p className="text-xs text-ink-400">This world also keeps {state.in_world_only.length} entr{state.in_world_only.length === 1 ? "y" : "ies"} no longer in the library: {state.in_world_only.map((e) => e.name).join(", ")}.</p>
            )}
            <div className="border-t border-ink-800 pt-3 text-xs text-ink-400">
              Worlds keep the homebrew they were built with.{" "}
              <button className="text-ember-300 hover:underline" disabled={busy}
                onClick={() => run(async () => { const r = await api.copyHomebrewToWorld(); await load(); return `Copied ${r.copied} entries into ${r.campaign}.`; })}>
                Copy the library into this world
              </button>{" "}to use new or changed entries in the current campaign.
            </div>
          </div>
        )}
        {note && <p className="mt-3 text-sm text-emerald-300">{note}</p>}
        <div className="mt-2"><ErrorBox error={error} /></div>
      </Card>

      <Card title={draft ? (editingUid ? `Edit ${draft.name || ONE[draft.kind]}` : `New ${ONE[draft.kind]}`) : "Editor"}
        subtitle={draft ? (draft.derived_from ? `Started from ${draft.derived_from} (keeps its CC-BY attribution)` : "Your own content") : undefined}
        actions={draft && <Button variant="ghost" onClick={() => setDraft(null)}>✕</Button>}>
        {!draft ? <Empty>Pick an entry to edit, or add a new creature, people or treasure.</Empty> : (
          <div className="space-y-3">
            {!editingUid && (
              <Field label="Start from an SRD entry (optional)">
                <input className={inputCls} placeholder={`Search SRD ${KIND_LABEL[draft.kind].toLowerCase()}…`} value={srdQ}
                  onChange={(e) => setSrdQ(e.target.value)} />
                {!!srdHits.length && (
                  <ul className="mt-1 max-h-40 overflow-auto rounded-lg border border-ink-700 text-sm">
                    {srdHits.map((h) => (
                      <li key={h.id}><button className="flex w-full justify-between px-2 py-1 text-left hover:bg-ink-800" onClick={() => fromSrd(h.id)}>
                        <span>{h.name}</span><span className="text-xs text-ink-400">{h.detail}</span></button></li>
                    ))}
                  </ul>
                )}
              </Field>
            )}
            <Field label="Name"><input className={inputCls} value={draft.name} maxLength={80} onChange={(e) => set("name", e.target.value)} /></Field>
            {draft.kind === "monster" && <>
              <div className="grid grid-cols-3 gap-2">
                <Field label="Size"><select className={inputCls} value={String(draft.size ?? "Medium")} onChange={(e) => set("size", e.target.value)}>
                  {state?.sizes.map((s) => <option key={s}>{s}</option>)}</select></Field>
                <Field label="Type"><input className={inputCls} value={String(draft.type ?? "")} onChange={(e) => set("type", e.target.value)} /></Field>
                <Field label="Challenge"><input className={inputCls} value={String(draft.cr ?? "")} onChange={(e) => set("cr", e.target.value)} /></Field>
                <Field label="Armour class"><input type="number" className={inputCls} value={Number(draft.ac ?? 10)} onChange={(e) => set("ac", +e.target.value)} /></Field>
                <Field label="Hit points"><input type="number" className={inputCls} value={Number(draft.hp ?? 1)} onChange={(e) => set("hp", +e.target.value)} /></Field>
                <Field label="Speed"><input className={inputCls} value={String(draft.speed ?? "")} onChange={(e) => set("speed", e.target.value)} /></Field>
              </div>
              <Field label="Lives in" hint="New worlds place it in the wilds of these biomes; none ticked means anywhere">
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(state?.biomes ?? {}).map(([id, name]) => {
                    const on = ((draft.biomes as number[]) ?? []).includes(+id);
                    return <button key={id} type="button" onClick={() => set("biomes", on ? (draft.biomes as number[]).filter((b) => b !== +id) : [...((draft.biomes as number[]) ?? []), +id])}
                      className={cx("rounded-full px-2 py-0.5 text-xs ring-1", on ? "bg-ember-500/80 text-ink-950 ring-ember-400" : "text-ink-300 ring-ink-700 hover:text-ink-100")}>{name}</button>;
                  })}
                </div>
              </Field>
              <Field label="Actions" hint="One per line"><textarea className={cx(inputCls, "h-20")} value={lines(draft.actions)} onChange={(e) => set("actions", e.target.value)} /></Field>
            </>}
            {draft.kind === "species" && <div className="grid grid-cols-3 gap-2">
              <Field label="Size"><select className={inputCls} value={String(draft.size ?? "Medium")} onChange={(e) => set("size", e.target.value)}>
                {state?.sizes.map((s) => <option key={s}>{s}</option>)}</select></Field>
              <Field label="Speed"><input className={inputCls} value={String(draft.speed ?? "")} onChange={(e) => set("speed", e.target.value)} /></Field>
              <Field label="Creature type"><input className={inputCls} value={String(draft.creature_type ?? "")} onChange={(e) => set("creature_type", e.target.value)} /></Field>
            </div>}
            {draft.kind === "item" && <div className="grid grid-cols-3 gap-2">
              <Field label="Rarity"><select className={inputCls} value={String(draft.rarity ?? "Uncommon")} onChange={(e) => set("rarity", e.target.value)}>
                {state?.rarities.map((r) => <option key={r}>{r}</option>)}</select></Field>
              <Field label="Category"><input className={inputCls} value={String(draft.category ?? "")} onChange={(e) => set("category", e.target.value)} /></Field>
              <label className="flex items-end gap-2 pb-2 text-sm text-ink-300"><input type="checkbox" className="accent-amber-500"
                checked={!!draft.attunement} onChange={(e) => set("attunement", e.target.checked)} />Requires attunement</label>
            </div>}
            {draft.kind !== "item" && (
              <Field label="Traits" hint="One per line"><textarea className={cx(inputCls, "h-20")} value={lines(draft.traits)} onChange={(e) => set("traits", e.target.value)} /></Field>
            )}
            <Field label="Description"><textarea className={cx(inputCls, "h-24")} value={draft.description ?? ""} onChange={(e) => set("description", e.target.value)} /></Field>
            {draft.derived_from && (
              <label className="flex items-center gap-2 text-sm text-ink-300">
                <input type="checkbox" className="accent-amber-500" checked={draft.overrides === draft.derived_from}
                  onChange={(e) => set("overrides", e.target.checked ? draft.derived_from : null)} />
                Replace {draft.derived_from} in new worlds (instead of adding beside it)
              </label>
            )}
            <div className="flex items-center gap-2">
              <Button variant="primary" disabled={busy || !draft.name.trim()} onClick={save}>{busy ? <Spinner /> : "Save"}</Button>
              {editingUid && <Button variant="ghost" disabled={busy} onClick={() => run(async () => {
                if (!confirm(`Delete ${draft.name} from the library? Worlds that already have a copy keep it.`)) return;
                await api.deleteHomebrew(editingUid); setDraft(null); setEditingUid(null); await load();
                return "Deleted from the library.";
              })}>Delete</Button>}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
