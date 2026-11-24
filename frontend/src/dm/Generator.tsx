import { useState } from 'react'
import { api } from '../lib/api'
import type { AppConfig } from '../lib/types'
import { Button, Panel, Toggle } from './ui'

const IDEAS = [
  'flooded goblin cavern with a rickety wooden bridge',
  'abandoned dwarven forge with lava channels',
  'misty forest road ambush at dusk',
  'smugglers docks at night with lanterns',
  'ruined temple courtyard overgrown with vines',
]

export function Generator({ config, toast }: { config: AppConfig | null; toast: (m: string, l?: 'info' | 'warn' | 'error' | 'success') => void }) {
  const [description, setDescription] = useState('')
  const [style, setStyle] = useState('')
  const [animate, setAnimate] = useState(true)
  const [push, setPush] = useState(false)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (description.trim().length < 3) return
    setBusy(true)
    try {
      await api.generate({ description: description.trim(), style: style || config?.styles[0] || '', animate, push })
      toast('Map generation queued', 'info')
      setDescription('')
    } catch (e) {
      toast(`Could not queue: ${(e as Error).message}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="Improvised Map Generator"
      actions={config?.mock && <span className="rounded bg-fuchsia-500/20 px-2 py-0.5 text-[10px] font-bold uppercase text-fuchsia-300">mock AI</span>}
    >
      <div className="space-y-3">
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          onKeyDown={(e) => (e.ctrlKey || e.metaKey) && e.key === 'Enter' && submit()}
          rows={3}
          maxLength={600}
          placeholder="Describe the area… e.g. flooded goblin cavern with a rickety wooden bridge"
          className="w-full resize-y rounded-lg bg-slate-800 px-3 py-2 text-sm ring-1 ring-white/10 outline-none placeholder:text-slate-500 focus:ring-amber-400"
        />
        <div className="flex flex-wrap gap-1.5">
          {IDEAS.map((i) => (
            <button
              key={i}
              type="button"
              onClick={() => setDescription(i)}
              className="rounded-full bg-white/5 px-2.5 py-1 text-xs text-slate-400 ring-1 ring-white/10 hover:bg-white/10 hover:text-slate-200"
            >
              {i}
            </button>
          ))}
        </div>
        <select
          value={style || config?.styles[0] || ''}
          onChange={(e) => setStyle(e.target.value)}
          className="w-full rounded-lg bg-slate-800 px-3 py-2 text-sm ring-1 ring-white/10 outline-none focus:ring-amber-400"
        >
          {(config?.styles ?? []).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <div className="grid grid-cols-2 gap-x-6">
          <Toggle label="Animate loop" checked={animate} onChange={setAnimate} />
          <Toggle label="Push when done" checked={push} onChange={setPush} />
        </div>
        <Button variant="primary" className="w-full py-2" disabled={busy || description.trim().length < 3} onClick={submit}>
          {busy ? 'Queuing…' : '✦ Generate battle map'}
        </Button>
        <p className="text-xs text-slate-500">
          LLM expands this into a strictly top-down 140×80 ft prompt → Flux paints the 4K base → Wan 2.1 FLF2V animates
          a seamless locked-camera loop.
        </p>
      </div>
    </Panel>
  )
}
