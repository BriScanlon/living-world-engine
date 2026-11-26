import { useEffect, useRef, useState } from 'react'
import { FogCanvas } from '../components/FogCanvas'
import { FxCanvas } from '../components/FxCanvas'
import { GridCanvas } from '../components/GridCanvas'
import { MapMedia } from '../components/MapMedia'
import { DEFAULT_VIEW, mapRect, useNaturalSize } from '../lib/mapView'
import { useTableSync } from '../lib/sync'
import { useDevicePixelRatio, useElementSize } from '../lib/useElementSize'
import { CalibrationOverlay } from './CalibrationOverlay'

const CURSOR_HIDE_MS = 2500

/**
 * /table — fullscreen kiosk for the 32" 4K tabletop monitor.
 *   Layer 1  map media (looping video / 4K still)
 *   Layer 2  ambient FX canvas
 *   Layer 3  calibrated 1" grid
 *   Layer 4  fog of war
 *   (top)    calibration ruler when enabled from /dm
 */
export function TablePage() {
  const { state, connected, send, fog, fogMeta } = useTableSync('table')
  const rootRef = useRef<HTMLDivElement>(null)
  const size = useElementSize(rootRef)
  const dpr = useDevicePixelRatio()
  const [cursorVisible, setCursorVisible] = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(!!document.fullscreenElement)

  // Tell the server our real viewport so the DM preview + brush sizes match exactly.
  useEffect(() => {
    if (connected && size.w > 0) send({ type: 'viewport', viewport: { w: size.w, h: size.h, dpr } })
  }, [connected, size.w, size.h, dpr, send])

  // Auto-hide the cursor.
  useEffect(() => {
    let timer = window.setTimeout(() => setCursorVisible(false), CURSOR_HIDE_MS)
    const onMove = () => {
      setCursorVisible(true)
      window.clearTimeout(timer)
      timer = window.setTimeout(() => setCursorVisible(false), CURSOR_HIDE_MS)
    }
    window.addEventListener('pointermove', onMove)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.clearTimeout(timer)
    }
  }, [])

  // Fullscreen on first click + keep the screen awake.
  useEffect(() => {
    let lock: { release: () => Promise<void> } | null = null
    const requestWake = async () => {
      try {
        lock = await (navigator as any).wakeLock?.request('screen')
      } catch {
        /* needs a secure context; kiosk-mode browsers usually disable sleep anyway */
      }
    }
    const onFs = () => setIsFullscreen(!!document.fullscreenElement)
    const onVis = () => document.visibilityState === 'visible' && requestWake()
    document.addEventListener('fullscreenchange', onFs)
    document.addEventListener('visibilitychange', onVis)
    requestWake()
    return () => {
      document.removeEventListener('fullscreenchange', onFs)
      document.removeEventListener('visibilitychange', onVis)
      void lock?.release()
    }
  }, [])

  const goFullscreen = () => {
    if (!document.fullscreenElement) void document.documentElement.requestFullscreen?.().catch(() => undefined)
  }

  const hexPx = state?.calibration.hexFlatToFlatPx ?? 91.8
  const table = { w: size.w || 1, h: size.h || 1, dpr }
  const [natural, onNatural] = useNaturalSize(state?.map ?? null)
  const rect = mapRect(state?.view ?? DEFAULT_VIEW, natural, table, hexPx)

  return (
    <div
      ref={rootRef}
      className="table-root"
      style={{ cursor: cursorVisible ? 'default' : 'none' }}
      onClick={goFullscreen}
      onContextMenu={(e) => e.preventDefault()}
    >
      {state?.map ? (
        <MapMedia map={state.map} rect={rect} playbackRate={state.display.playbackRate} onNaturalSize={onNatural} />
      ) : (
        <IdleScreen />
      )}

      {state && <FxCanvas fx={state.fx} width={size.w} height={size.h} />}

      {state && (
        <GridCanvas grid={state.grid} hexPx={hexPx} table={table} width={size.w} height={size.h} dpr={dpr} />
      )}

      <FogCanvas
        store={fog}
        enabled={fogMeta.enabled}
        rect={rect}
        resolution={1}
        color="#000000"
        opacity={1}
      />

      {state?.calibration.active && (
        <CalibrationOverlay type={state.grid.type} hexPx={hexPx} width={size.w} height={size.h} dpr={dpr} />
      )}

      {!connected && (
        <div className="absolute top-4 right-4 rounded-full bg-red-600/90 px-4 py-2 text-sm font-semibold text-white shadow-lg">
          Reconnecting to VTT server…
        </div>
      )}
      {cursorVisible && !isFullscreen && (
        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 rounded-full bg-black/70 px-5 py-2 text-sm text-slate-200 ring-1 ring-white/20">
          Click anywhere for fullscreen · open <span className="font-mono text-amber-300">{location.host}/dm</span> on
          the DM's device
        </div>
      )}
    </div>
  )
}

function IdleScreen() {
  return (
    <div className="layer flex flex-col items-center justify-center bg-[radial-gradient(ellipse_at_center,#1e293b_0%,#020617_70%)]">
      <svg viewBox="0 0 64 64" className="h-32 w-32 opacity-80">
        <polygon points="32,6 54.5,19 54.5,45 32,58 9.5,45 9.5,19" fill="none" stroke="#f59e0b" strokeWidth="3" />
        <circle cx="32" cy="32" r="5" fill="#f59e0b" />
      </svg>
      <p className="mt-6 text-3xl font-semibold tracking-wide text-slate-300">Waiting for the Dungeon Master…</p>
    </div>
  )
}
