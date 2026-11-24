import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useTableSync } from '../lib/sync'
import type { AppConfig } from '../lib/types'
import { FogEditor } from './FogEditor'
import { Generator } from './Generator'
import { Importer } from './Importer'
import { Jobs } from './Jobs'
import { Library } from './Library'
import { useNaturalSize } from '../lib/mapView'
import { CalibrationControls, DisplayControls, FxControls, GridControls, MapScaleControls } from './TableControls'

/** /dm — the Dungeon Master's control panel (laptop / tablet). */
export function DmPage() {
  const sync = useTableSync('dm')
  const [natural, onNatural] = useNaturalSize(sync.state?.map ?? null)
  const { state, connected, toasts, toast } = sync
  const [config, setConfig] = useState<AppConfig | null>(null)

  useEffect(() => {
    api.config().then(setConfig).catch(() => undefined)
  }, [])

  useEffect(() => {
    document.title = 'DM · Local-AI VTT'
  }, [])

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
        <a href="/table" target="_blank" rel="noreferrer" className="ml-auto text-sm text-amber-300 hover:text-amber-200">
          Open /table ↗
        </a>
      </header>

      {!state ? (
        <div className="flex h-[60vh] items-center justify-center text-slate-500">Connecting to VTT server…</div>
      ) : (
        <main className="mx-auto grid max-w-[1800px] gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_400px]">
          <div className="min-w-0 space-y-4">
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
            <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              <MapScaleControls state={state} patch={sync.patch} natural={natural} />
              <GridControls state={state} patch={sync.patch} />
              <CalibrationControls state={state} send={sync.send} defaultHexPx={config?.defaultHexPx ?? 91.8} />
              <div className="space-y-4">
                <FxControls state={state} patch={sync.patch} />
                <DisplayControls state={state} patch={sync.patch} />
              </div>
            </div>
          </div>
          <aside className="space-y-4">
            <Generator config={config} toast={toast} />
            <Jobs jobs={sync.jobs} />
            <Importer toast={toast} watchInbox={config?.watchInbox ?? true} />
          </aside>
          <div className="xl:col-span-2">
            <Library version={sync.libraryVersion} currentMapId={state.map?.id ?? null} toast={toast} />
          </div>
        </main>
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
