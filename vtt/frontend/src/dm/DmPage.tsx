import { useEffect, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { useTableSync } from '../lib/sync'
import type { AppConfig } from '../lib/types'
import { FogEditor } from './FogEditor'
import { Generator } from './Generator'
import { Importer } from './Importer'
import { Jobs } from './Jobs'
import { Library } from './Library'
import { WorldPanel } from './WorldPanel'
import { SessionLog } from './SessionLog'
import { useNaturalSize } from '../lib/mapView'
import { CalibrationControls, DisplayControls, FxControls, GridControls, MapScaleControls } from './TableControls'
import { ArrangeProvider, ZoneView } from './Arrange'
import { DEFAULT_LAYOUT, loadLayout, normalize, sameLayout, saveLayout, type Layout, type PanelId } from './layout'
import { Button } from './ui'

/** /dm — the Dungeon Master's control panel (laptop / tablet). */
export function DmPage() {
  const sync = useTableSync('dm')
  const [natural, onNatural] = useNaturalSize(sync.state?.map ?? null)
  const { state, connected, toasts, toast } = sync
  const [config, setConfig] = useState<AppConfig | null>(null)
  // Panel layout: `saved` is what's in use; `draft` exists only while in layout edit mode.
  const [saved, setSaved] = useState<Layout>(loadLayout)
  const [draft, setDraft] = useState<Layout | null>(null)
  const editing = draft !== null
  const layout = draft ?? saved

  const startEditing = () => setDraft(saved)
  const cancelEditing = () => setDraft(null)
  const saveEditing = () => {
    if (draft) {
      saveLayout(draft)
      setSaved(draft)
    }
    setDraft(null)
    toast('Layout saved', 'success')
  }

  useEffect(() => {
    if (!editing) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setDraft(null)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editing])

  useEffect(() => {
    api.config().then(setConfig).catch(() => undefined)
  }, [])

  useEffect(() => {
    document.title = 'DM · Local-AI VTT'
  }, [])

  const renderPanel = (id: PanelId): ReactNode => {
    if (!state) return null
    switch (id) {
      case 'preview':
        return (
          <FogEditor
            state={state}
            fog={sync.fog}
            fogMeta={sync.fogMeta}
            sendFogOp={sync.sendFogOp}
            send={sync.send}
            patch={sync.patch}
            natural={natural}
            onNatural={onNatural}
          />
        )
      case 'mapScale':
        return <MapScaleControls state={state} patch={sync.patch} natural={natural} />
      case 'grid':
        return <GridControls state={state} patch={sync.patch} />
      case 'calibration':
        return <CalibrationControls state={state} send={sync.send} defaultHexPx={config?.defaultHexPx ?? 91.8} />
      case 'fx':
        return <FxControls state={state} patch={sync.patch} />
      case 'display':
        return <DisplayControls state={state} patch={sync.patch} />
      case 'library':
        return <Library version={sync.libraryVersion} currentMapId={state.map?.id ?? null} toast={toast} />
      case 'generator':
        return <Generator config={config} toast={toast} />
      case 'jobs':
        return <Jobs jobs={sync.jobs} />
      case 'importer':
        return <Importer toast={toast} watchInbox={config?.watchInbox ?? true} />
      case 'world':
        return <WorldPanel scene={sync.scene} />
      case 'sessionLog':
        return <SessionLog entries={sync.log} toast={toast} />
    }
  }

  return (
    <div className="min-h-full bg-[#0b0d12] text-slate-200">
      <header className="sticky top-0 z-20 flex flex-wrap items-center gap-3 border-b border-white/10 bg-[#0b0d12]/90 px-4 py-2.5 backdrop-blur">
        <svg viewBox="0 0 64 64" className="h-7 w-7">
          <polygon points="32,6 54.5,19 54.5,45 32,58 9.5,45 9.5,19" fill="none" stroke="#f59e0b" strokeWidth="5" />
          <circle cx="32" cy="32" r="6" fill="#f59e0b" />
        </svg>
        <h1 className="text-base font-semibold tracking-wide">DM Control Panel</h1>
        <StatusPill ok={connected} label={connected ? 'Server connected' : 'Reconnecting…'} />
        {state && (
          <StatusPill
            ok={state.clients.table > 0}
            label={state.clients.table > 0 ? `Table online (${state.clients.table})` : 'No table connected'}
          />
        )}
        {config?.mock && (
          <span className="rounded bg-fuchsia-500/20 px-2 py-0.5 text-xs font-semibold text-fuchsia-300">
            MOCK_AI_PIPELINE
          </span>
        )}
        <div className="ml-auto flex items-center gap-3">
          {!editing && state && (
            <Button variant="subtle" onClick={startEditing} title="Rearrange and move the DM panels">
              ⊞ Edit layout
            </Button>
          )}
          <a href="/table" target="_blank" rel="noreferrer" className="text-sm text-amber-300 hover:text-amber-200">
            Open /table ↗
          </a>
        </div>
      </header>

      {editing && draft && (
        <div className="sticky top-[49px] z-20 flex flex-wrap items-center gap-3 border-b border-amber-400/30 bg-amber-950/90 px-4 py-2 backdrop-blur">
          <span className="text-sm font-semibold text-amber-200">Layout edit mode</span>
          <span className="text-xs text-amber-200/70">
            Drag panels to reorder or move them between the main area and the sidebar, or use the arrows. Esc cancels.
          </span>
          <div className="ml-auto flex gap-2">
            <Button
              variant="subtle"
              disabled={sameLayout(draft, normalize(DEFAULT_LAYOUT))}
              onClick={() => setDraft(normalize(DEFAULT_LAYOUT))}
            >
              Reset to default
            </Button>
            <Button onClick={cancelEditing}>Cancel</Button>
            <Button variant="primary" onClick={saveEditing} disabled={sameLayout(draft, saved)}>
              Save layout
            </Button>
          </div>
        </div>
      )}

      {!state ? (
        <div className="flex h-[60vh] items-center justify-center text-slate-500">Connecting to VTT server…</div>
      ) : (
        <ArrangeProvider value={{ editing, layout, setLayout: setDraft }}>
          <main className="mx-auto grid max-w-[1800px] items-start gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_400px]">
            <ZoneView zone="main" className="grid min-w-0 items-start gap-4 md:grid-cols-2 2xl:grid-cols-3" render={renderPanel} />
            <ZoneView zone="side" className="grid min-w-0 items-start gap-4" render={renderPanel} />
          </main>
        </ArrangeProvider>
      )}

      <div className="fixed right-4 bottom-4 z-50 flex w-80 flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`rounded-lg px-4 py-2.5 text-sm shadow-xl ring-1 ${
              t.level === 'error'
                ? 'bg-red-950 text-red-200 ring-red-500/40'
                : t.level === 'success'
                  ? 'bg-emerald-950 text-emerald-200 ring-emerald-500/40'
                  : t.level === 'warn'
                    ? 'bg-amber-950 text-amber-200 ring-amber-500/40'
                    : 'bg-slate-800 text-slate-200 ring-white/10'
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </div>
  )
}

function StatusPill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="flex items-center gap-1.5 rounded-full bg-white/5 px-2.5 py-0.5 text-xs text-slate-300 ring-1 ring-white/10">
      <span className={`h-2 w-2 rounded-full ${ok ? 'bg-emerald-400' : 'bg-red-500 animate-pulse'}`} />
      {label}
    </span>
  )
}
