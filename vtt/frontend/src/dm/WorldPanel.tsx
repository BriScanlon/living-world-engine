import { useState } from 'react'
import type { Scene, Token } from '../lib/types'
import { Button, Panel } from './ui'

const ACCURACY: Record<string, string> = {
  True: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/40',
  Exaggerated: 'bg-amber-500/15 text-amber-300 ring-amber-500/40',
  False: 'bg-red-500/15 text-red-300 ring-red-500/40',
}

/** World context for the place on the table, sent from the Living World Engine's atlas. DM eyes only. */
export function WorldPanel({ scene, send, tokens, hasMap }: {
  scene: Scene | null
  send: (msg: unknown) => void
  tokens: Token[]
  hasMap: boolean
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({ people: true, rumors: true })
  if (!scene) {
    return (
      <Panel title="World">
        <p className="text-sm text-slate-400">
          No place selected. In the Living World Engine's <b>World Atlas</b>, open a town or building and press
          <b> Send scene to the VTT</b>: its battle map appears here and on the table, with who is there, who holds it and
          what the taverns are saying.
        </p>
      </Panel>
    )
  }
  const onTable = new Set(tokens.map((t) => t.asset_id).filter(Boolean))
  const placeable = (scene.people ?? []).filter((p) => p.portrait_id && !onTable.has(p.portrait_id))
  const place = (assetId: string) => send({ type: 'token_add', token: { asset_id: assetId } })

  const section = (key: string, title: string, count: number, body: React.ReactNode) =>
    count > 0 && (
      <div className="border-t border-white/10 pt-2">
        <button className="flex w-full items-center justify-between text-left text-xs font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-200"
          onClick={() => setOpen((o) => ({ ...o, [key]: !o[key] }))}>
          <span>{title} ({count})</span><span>{open[key] ? '−' : '+'}</span>
        </button>
        {open[key] && <div className="mt-2">{body}</div>}
      </div>
    )

  return (
    <Panel title="World" actions={<span className="text-[11px] text-slate-500">day {scene.day ?? '–'}{scene.branch && scene.branch !== 'main' ? ` · ${scene.branch}` : ''}</span>}>
      <div className="space-y-3 text-sm">
        <div>
          <div className="text-lg font-semibold text-amber-300">{scene.place.name}</div>
          <div className="text-xs text-slate-400">
            {[scene.place.kind, scene.province, scene.realm].filter(Boolean).join(' · ')}
          </div>
          {scene.holder && <div className="mt-1 text-xs text-slate-300">Held by <b>{scene.holder}</b></div>}
          {scene.place.description && <p className="mt-1 text-slate-300">{scene.place.description}</p>}
          {scene.map?.title && <p className="mt-1 text-xs text-slate-500">On the table: {scene.map.title}</p>}
        </div>

        {section('people', 'Who is here', scene.people?.length ?? 0, (
          <ul className="space-y-2">
            {hasMap && placeable.length > 1 && (
              <li>
                <Button className="w-full !py-1 text-xs" onClick={() => placeable.forEach((p) => place(p.portrait_id!))}>
                  ◉ Put everyone on the table ({placeable.length})
                </Button>
              </li>
            )}
            {scene.people!.map((p, i) => (
              <li key={i} className="flex gap-2">
                {p.portrait && <img src={p.portrait} alt="" className="h-10 w-10 shrink-0 rounded-full ring-1 ring-white/20" />}
                <div className="min-w-0">
                <div className="text-slate-100">{p.name} <span className="text-xs text-slate-400">{p.role}</span></div>
                <div className="text-xs text-slate-400">
                  {[p.species, p.char_class, p.faction].filter(Boolean).join(' · ')}
                  {p.stats && <> · <span className="font-mono">{p.stats.name}: AC {p.stats.ac} HP {p.stats.hp} CR {p.stats.cr}</span></>}
                </div>
                {!!p.wants?.length && <div className="text-xs text-sky-300">wants {p.wants.join('; ')}</div>}
                {!!p.fears?.length && <div className="text-xs text-rose-300">fears {p.fears.join('; ')}</div>}
                </div>
                {hasMap && p.portrait_id && (
                  onTable.has(p.portrait_id)
                    ? <span className="ml-auto shrink-0 self-start text-[10px] text-emerald-400">on table</span>
                    : <Button variant="subtle" className="ml-auto shrink-0 self-start !px-1.5 !py-0.5 text-xs" title="Put their token on the table"
                        onClick={() => place(p.portrait_id!)}>◉ Place</Button>
                )}
              </li>
            ))}
          </ul>
        ))}

        {section('tensions', 'Tensions', scene.tensions?.length ?? 0, (
          <ul className="space-y-1 text-xs text-slate-300">
            {scene.tensions!.map((t, i) => <li key={i}>{t.between} → {t.and}: <span className={t.trust < 0 ? 'text-rose-300' : 'text-emerald-300'}>{t.label} ({t.trust})</span></li>)}
          </ul>
        ))}

        {section('rumors', 'Rumors to read aloud', scene.rumors?.length ?? 0, (
          <ul className="space-y-2">
            {scene.rumors!.map((r, i) => (
              <li key={i} className="rounded-md bg-white/5 p-2">
                <div className="italic text-slate-100">{r.dialogue}</div>
                <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
                  {r.speaker && <span>— {r.speaker}</span>}
                  {r.accuracy && <span className={`rounded px-1.5 ring-1 ring-inset ${ACCURACY[r.accuracy] ?? ''}`}>{r.accuracy}</span>}
                </div>
              </li>
            ))}
          </ul>
        ))}

        {section('creatures', 'Creatures (SRD)', scene.creatures?.length ?? 0, (
          <ul className="space-y-1 text-xs text-slate-300">
            {scene.creatures!.map((c, i) => (
              <li key={i}>{c.name}{c.stats && <span className="font-mono text-slate-400"> · AC {c.stats.ac} HP {c.stats.hp} CR {c.stats.cr}</span>}</li>
            ))}
          </ul>
        ))}

        {section('events', 'Recent events', scene.events?.length ?? 0, (
          <ul className="space-y-1 text-xs text-slate-300">
            {scene.events!.map((e, i) => <li key={i}><span className="font-mono text-slate-500">d{e.day}</span> {e.title}</li>)}
          </ul>
        ))}

        {section('goals', 'Plans in motion', scene.goals?.length ?? 0, (
          <ul className="space-y-1 text-xs text-slate-300">
            {scene.goals!.map((g, i) => <li key={i}>{g.owner}: {g.name} <span className="font-mono text-slate-500">{g.clock}</span></li>)}
          </ul>
        ))}

        {scene.attribution && <p className="border-t border-white/10 pt-2 text-[10px] leading-snug text-slate-500">{scene.attribution}</p>}
      </div>
    </Panel>
  )
}
