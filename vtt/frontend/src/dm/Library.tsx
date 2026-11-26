import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Asset } from '../lib/types'
import { Button, Panel, Segmented } from './ui'

type Toast = (m: string, l?: 'info' | 'warn' | 'error' | 'success') => void

export function Library({ version, currentMapId, toast }: { version: number; currentMapId: string | null; toast: Toast }) {
  const [kind, setKind] = useState<'map' | 'token'>('map')
  const [assets, setAssets] = useState<Asset[]>([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .assets(kind)
      .then((a) => !cancelled && setAssets(a))
      .catch((e) => toast(`Library: ${(e as Error).message}`, 'error'))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [kind, version, toast])

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase()
    if (!q) return assets
    return assets.filter((a) => `${a.title} ${a.description ?? ''} ${a.source} ${a.pack ?? ''}`.toLowerCase().includes(q))
  }, [assets, filter])

  const push = async (a: Asset) => {
    try {
      if (a.asset_type === 'token') {
        if (!currentMapId) return toast('Push a map to the table first, then place tokens on it.', 'warn')
        await api.placeToken({ asset_id: a.id })
        return toast(`Placed ${a.title} on the table (Table Preview → Tokens to move it)`, 'success')
      }
      await api.push(a.id)
    } catch (e) {
      toast((e as Error).message, 'error')
    }
  }
  const rename = async (a: Asset) => {
    const title = window.prompt('Rename map', a.title)?.trim()
    if (!title || title === a.title) return
    await api.rename(a.id, title).catch((e) => toast((e as Error).message, 'error'))
  }
  const remove = async (a: Asset) => {
    if (!window.confirm(`Delete “${a.title}” and its files? This cannot be undone.`)) return
    await api.remove(a.id).catch((e) => toast((e as Error).message, 'error'))
  }

  return (
    <Panel
      title={`Live ${kind === 'map' ? 'Map' : 'Token'} Library`}
      actions={
        <>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter…"
            className="w-36 rounded-lg bg-slate-800 px-2.5 py-1 text-sm ring-1 ring-white/10 outline-none focus:ring-amber-400 sm:w-48"
          />
          <div className="w-40">
            <Segmented
              value={kind}
              onChange={setKind}
              options={[
                { value: 'map', label: 'Maps' },
                { value: 'token', label: 'Tokens' },
              ]}
            />
          </div>
          {kind === 'map' && (
            <Button variant="subtle" onClick={() => api.clearTable()} disabled={!currentMapId}>
              Clear table
            </Button>
          )}
        </>
      }
    >
      {!loading && shown.length === 0 && (
        <p className="py-8 text-center text-sm text-slate-500">
          {assets.length === 0
            ? kind === 'map'
              ? 'No maps yet — generate one, import an Inkarnate export, or run scripts/seed_starter_assets.py.'
              : 'No tokens yet — run scripts/seed_starter_assets.py to create the starter tokens.'
            : 'Nothing matches that filter.'}
        </p>
      )}
      <div
        className={`grid gap-3 ${
          kind === 'map'
            ? 'grid-cols-[repeat(auto-fill,minmax(220px,1fr))]'
            : 'grid-cols-[repeat(auto-fill,minmax(120px,1fr))]'
        }`}
      >
        {shown.map((a) =>
          kind === 'map' ? (
            <MapCard key={a.id} asset={a} onTable={a.id === currentMapId} onPush={push} onRename={rename} onDelete={remove} />
          ) : (
            <TokenCard key={a.id} asset={a} onDelete={remove} />
          ),
        )}
      </div>
    </Panel>
  )
}

function MapCard({
  asset: a,
  onTable,
  onPush,
  onRename,
  onDelete,
}: {
  asset: Asset
  onTable: boolean
  onPush: (a: Asset) => void
  onRename: (a: Asset) => void
  onDelete: (a: Asset) => void
}) {
  const [hover, setHover] = useState(false)
  const video = useRef<HTMLVideoElement>(null)
  const isVideo = a.media_type === 'video'

  useEffect(() => {
    const v = video.current
    if (!v) return
    if (hover) void v.play().catch(() => undefined)
    else v.pause()
  }, [hover])

  return (
    <div
      className={`group overflow-hidden rounded-lg bg-slate-800/70 ring-1 transition ${onTable ? 'ring-2 ring-amber-400' : 'ring-white/10 hover:ring-white/25'}`}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div className="relative aspect-video bg-black">
        {a.thumb_url && <img src={a.thumb_url} alt="" className="absolute inset-0 h-full w-full object-cover" loading="lazy" />}
        {isVideo && (
          // animated preview: plays on hover (or always, when there is no still thumbnail)
          <video
            ref={video}
            src={a.media_url}
            muted
            loop
            playsInline
            preload={a.thumb_url ? 'none' : 'metadata'}
            autoPlay={!a.thumb_url}
            className={`absolute inset-0 h-full w-full object-cover transition-opacity ${hover || !a.thumb_url ? 'opacity-100' : 'opacity-0'}`}
          />
        )}
        <div className="absolute top-1.5 left-1.5 flex gap-1">
          <Badge tone={isVideo ? 'emerald' : 'slate'}>{isVideo ? '▶ loop' : 'static'}</Badge>
          {a.pack && <Badge tone="sky">{a.pack}</Badge>}
          {a.mock && <Badge tone="fuchsia">mock</Badge>}
        </div>
        {onTable && (
          <div className="absolute top-1.5 right-1.5">
            <Badge tone="amber">ON TABLE</Badge>
          </div>
        )}
        <button
          type="button"
          onClick={() => onPush(a)}
          className="absolute inset-0 flex items-center justify-center bg-black/50 text-sm font-semibold text-white opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
        >
          {a.asset_type === 'token' ? '◉ Place on Table' : '⇧ Push to Table'}
        </button>
      </div>
      <div className="flex items-center gap-1 px-2.5 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-slate-200" title={a.title}>
            {a.title}
          </div>
          <div className="truncate text-[11px] text-slate-500" title={a.description ?? a.motion_prompt ?? ''}>
            {a.source} · {new Date(a.created_at * 1000).toLocaleDateString()}
          </div>
        </div>
        <Button variant="primary" className="px-2 py-1 text-xs" onClick={() => onPush(a)}>
          {a.asset_type === 'token' ? 'Place' : 'Push'}
        </Button>
        <Button variant="subtle" className="px-1.5 py-1 text-xs" onClick={() => onRename(a)} title="Rename">
          ✎
        </Button>
        <Button variant="subtle" className="px-1.5 py-1 text-xs hover:text-red-400" onClick={() => onDelete(a)} title="Delete">
          🗑
        </Button>
      </div>
    </div>
  )
}

function TokenCard({ asset: a, onDelete }: { asset: Asset; onDelete: (a: Asset) => void }) {
  return (
    <div className="group relative rounded-lg bg-slate-800/70 p-2 text-center ring-1 ring-white/10">
      <img src={a.thumb_url ?? a.media_url} alt={a.title} className="mx-auto aspect-square w-full object-contain" loading="lazy" />
      <div className="mt-1 truncate text-xs text-slate-300" title={a.description ?? ''}>
        {a.title}
      </div>
      <a href={a.media_url} download className="text-[11px] text-amber-300/80 hover:text-amber-200">
        Download
      </a>
      <button
        type="button"
        onClick={() => onDelete(a)}
        className="absolute top-1 right-1 rounded px-1 text-xs text-slate-500 opacity-0 group-hover:opacity-100 hover:text-red-400"
        title="Delete"
      >
        ✕
      </button>
    </div>
  )
}

const tones = {
  slate: 'bg-slate-900/80 text-slate-300',
  emerald: 'bg-emerald-900/80 text-emerald-200',
  sky: 'bg-sky-900/80 text-sky-200',
  fuchsia: 'bg-fuchsia-900/80 text-fuchsia-200',
  amber: 'bg-amber-500 text-slate-950',
}
function Badge({ tone, children }: { tone: keyof typeof tones; children: React.ReactNode }) {
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${tones[tone]}`}>{children}</span>
}
