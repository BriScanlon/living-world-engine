import { useEffect, useState } from 'react'
import { expectedCssPxPerInch } from '../lib/hexGrid'
import { AI_SQUARES_ACROSS, FEET_PER_SQUARE, type Size } from '../lib/mapView'
import type { FxName, MapView, TableState } from '../lib/types'
import type { useTableSync } from '../lib/sync'
import { Button, Panel, Segmented, Slider, Toggle } from './ui'

type Sync = ReturnType<typeof useTableSync>

const FX_META: Record<FxName, { label: string; icon: string }> = {
  fog: { label: 'Drifting fog', icon: '☁️' },
  rain: { label: 'Rain', icon: '🌧️' },
  embers: { label: 'Floating embers', icon: '✨' },
  torch: { label: 'Torchlight flicker', icon: '🔥' },
}

export function GridControls({ state, patch }: { state: TableState; patch: Sync['patch'] }) {
  const g = state.grid
  const f = state.calibration.hexFlatToFlatPx
  return (
    <Panel title="Grid" actions={<Toggle label="" checked={g.visible} onChange={(v) => patch('grid', { visible: v })} />}>
      <div className="space-y-2">
        <Segmented
          value={g.type}
          onChange={(type) => patch('grid', { type })}
          options={[
            { value: 'hex-pointy', label: '⬡ Pointy-top' },
            { value: 'hex-flat', label: '⬢ Flat-top' },
            { value: 'square', label: '▦ Square' },
          ]}
        />
        <div className="flex items-center gap-3 pt-1">
          <label className="flex items-center gap-2 text-sm text-slate-300">
            Color
            <input
              type="color"
              value={g.color}
              onChange={(e) => patch('grid', { color: e.target.value })}
              className="h-8 w-12 cursor-pointer rounded border border-white/10 bg-transparent"
            />
          </label>
          {['#ffffff', '#000000', '#fde047', '#22d3ee'].map((c) => (
            <button
              key={c}
              type="button"
              aria-label={`grid color ${c}`}
              onClick={() => patch('grid', { color: c })}
              className="h-6 w-6 rounded-full ring-1 ring-white/30"
              style={{ background: c }}
            />
          ))}
        </div>
        <Slider label="Opacity" value={g.opacity} min={0} max={1} onChange={(v) => patch('grid', { opacity: v })} format={(v) => `${Math.round(v * 100)}%`} />
        <Slider label="Line thickness" value={g.lineWidth} min={0.5} max={8} step={0.25} onChange={(v) => patch('grid', { lineWidth: v })} format={(v) => `${v.toFixed(2)} px`} />
        <Slider label="Offset X" value={g.offsetX} min={-f} max={f} step={0.5} onChange={(v) => patch('grid', { offsetX: v })} format={(v) => `${v.toFixed(1)} px`} />
        <Slider label="Offset Y" value={g.offsetY} min={-f} max={f} step={0.5} onChange={(v) => patch('grid', { offsetY: v })} format={(v) => `${v.toFixed(1)} px`} />
        <div className="flex items-center justify-between">
          <div className="w-48">
            <Toggle label="Hex coordinates" checked={g.showCoords} onChange={(v) => patch('grid', { showCoords: v })} />
          </div>
          <Button variant="subtle" onClick={() => patch('grid', { offsetX: 0, offsetY: 0 })}>
            Reset offset
          </Button>
        </div>
      </div>
    </Panel>
  )
}

export function FxControls({ state, patch }: { state: TableState; patch: Sync['patch'] }) {
  return (
    <Panel title="Ambient FX">
      <div className="space-y-3">
        {(Object.keys(FX_META) as FxName[]).map((name) => {
          const fx = state.fx[name]
          return (
            <div key={name}>
              <Toggle
                label={
                  <span>
                    {FX_META[name].icon} {FX_META[name].label}
                  </span>
                }
                checked={fx.enabled}
                onChange={(v) => patch('fx', { [name]: { enabled: v } })}
              />
              <Slider
                label="Intensity"
                value={fx.intensity}
                min={0}
                max={1}
                onChange={(v) => patch('fx', { [name]: { intensity: v } })}
                format={(v) => `${Math.round(v * 100)}%`}
                disabled={!fx.enabled}
              />
            </div>
          )
        })}
      </div>
    </Panel>
  )
}

export function DisplayControls({ state, patch }: { state: TableState; patch: Sync['patch'] }) {
  return (
    <Panel title="Display">
      <div className="space-y-2">
        <Slider
          label="Loop playback speed"
          value={state.display.playbackRate}
          min={0.25}
          max={2}
          step={0.05}
          onChange={(v) => patch('display', { playbackRate: v })}
          format={(v) => `${v.toFixed(2)}×`}
        />
      </div>
    </Panel>
  )
}

const ZOOM_STEPS = [-10, -1, -0.1, 0.1, 1, 10] // percent

/**
 * Per-map scale so the painted 5 ft squares match the calibrated 1" grid.
 * Zoom works on `pxPerSquare` (image pixels per 5 ft): zooming in = fewer image px per inch.
 */
export function MapScaleControls({ state, patch, natural }: { state: TableState; patch: Sync['patch']; natural: Size | null }) {
  const v = state.view
  const map = state.map
  const tv = state.tableViewport
  const hexPx = state.calibration.hexFlatToFlatPx
  const across = natural ? natural.w / v.pxPerSquare : 0
  const down = natural ? natural.h / v.pxPerSquare : 0
  const [draftSquares, setDraftSquares] = useState('')
  const [draftPx, setDraftPx] = useState('')
  useEffect(() => {
    setDraftSquares(across ? across.toFixed(2) : '')
    setDraftPx(v.pxPerSquare.toFixed(2))
  }, [across, v.pxPerSquare])

  const set = (p: Partial<MapView>) => patch('view', p)
  const setPx = (px: number) => Number.isFinite(px) && px > 0 && set({ mode: 'scale', pxPerSquare: Math.round(px * 1000) / 1000 })
  const zoom = (pct: number) => setPx(v.pxPerSquare / (1 + pct / 100))
  // nudge the map by a tenth of a square (in image fraction)
  const nudge = (dx: number, dy: number) =>
    natural && set({ cx: v.cx - (dx * v.pxPerSquare) / 10 / natural.w, cy: v.cy - (dy * v.pxPerSquare) / 10 / natural.h })

  return (
    <Panel title={`Map Scale · 1" = ${FEET_PER_SQUARE} ft`}>
      {!map ? (
        <p className="text-sm text-slate-500">Push a map to the table to set its scale.</p>
      ) : (
        <div className="space-y-3">
          <Segmented
            value={v.mode}
            onChange={(mode) => set({ mode })}
            options={[
              { value: 'scale', label: '1" = 5 ft' },
              { value: 'cover', label: 'Fill screen' },
              { value: 'contain', label: 'Fit screen' },
            ]}
          />
          {v.mode !== 'scale' ? (
            <p className="text-xs leading-relaxed text-amber-300/80">
              Fill/Fit stretch the image to the screen, so its squares won't match your minis. Switch to{' '}
              <b>1" = 5 ft</b> and use the <b>📏 Measure</b> tool in the preview.
            </p>
          ) : (
            <>
              <div className="rounded-lg bg-slate-800/60 p-2.5 text-xs leading-relaxed text-slate-400">
                <div>
                  <span className="font-mono text-base text-amber-300">{v.pxPerSquare.toFixed(2)}</span> image px = 5 ft = 1"
                </div>
                {natural && (
                  <div>
                    Map: {across.toFixed(1)} × {down.toFixed(1)} squares ({Math.round(across * 5)} × {Math.round(down * 5)} ft)
                  </div>
                )}
                <div>
                  Table shows {(tv.w / hexPx).toFixed(1)} × {(tv.h / hexPx).toFixed(1)} squares
                </div>
              </div>

              <div>
                <div className="mb-1 text-xs text-slate-400">Zoom map</div>
                <div className="grid grid-cols-6 gap-1">
                  {ZOOM_STEPS.map((p) => (
                    <Button key={p} className="px-1 font-mono text-xs" onClick={() => zoom(p)} title={p > 0 ? 'Zoom in (map bigger)' : 'Zoom out (map smaller)'}>
                      {p > 0 ? '+' : ''}
                      {p}%
                    </Button>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-slate-400">
                  Squares across
                  <input
                    value={draftSquares}
                    onChange={(e) => setDraftSquares(e.target.value)}
                    onBlur={() => natural && setPx(natural.w / parseFloat(draftSquares))}
                    onKeyDown={(e) => e.key === 'Enter' && natural && setPx(natural.w / parseFloat(draftSquares))}
                    inputMode="decimal"
                    disabled={!natural}
                    className="mt-1 w-full rounded-lg bg-slate-800 px-2.5 py-1.5 font-mono text-sm text-slate-200 ring-1 ring-white/10 outline-none focus:ring-amber-400"
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Image px per square
                  <input
                    value={draftPx}
                    onChange={(e) => setDraftPx(e.target.value)}
                    onBlur={() => setPx(parseFloat(draftPx))}
                    onKeyDown={(e) => e.key === 'Enter' && setPx(parseFloat(draftPx))}
                    inputMode="decimal"
                    className="mt-1 w-full rounded-lg bg-slate-800 px-2.5 py-1.5 font-mono text-sm text-slate-200 ring-1 ring-white/10 outline-none focus:ring-amber-400"
                  />
                </label>
              </div>

              <div className="flex items-center gap-3">
                <div className="grid grid-cols-3 gap-1" title="Slide the map ⅒ square to line painted squares up with the grid">
                  <span />
                  <Button className="px-2 py-1 text-xs" onClick={() => nudge(0, -1)}>↑</Button>
                  <span />
                  <Button className="px-2 py-1 text-xs" onClick={() => nudge(-1, 0)}>←</Button>
                  <Button className="px-2 py-1 text-xs" onClick={() => set({ cx: 0.5, cy: 0.5 })} title="Centre map">
                    ⊙
                  </Button>
                  <Button className="px-2 py-1 text-xs" onClick={() => nudge(1, 0)}>→</Button>
                  <span />
                  <Button className="px-2 py-1 text-xs" onClick={() => nudge(0, 1)}>↓</Button>
                  <span />
                </div>
                <div className="flex flex-1 flex-col gap-1.5">
                  {natural && (
                    <Button onClick={() => setPx(natural.w / AI_SQUARES_ACROSS)} title="AI-generated and starter maps are painted 140 ft (28 squares) wide">
                      AI map preset (140 ft wide)
                    </Button>
                  )}
                  <p className="text-[11px] leading-snug text-slate-500">Arrows move the map ⅒ square to line painted squares up with the grid.</p>
                </div>
              </div>
            </>
          )}
          <p className="text-xs leading-relaxed text-slate-500">
            Fastest way: pick <b>📏 Measure</b> in the preview, drag across several painted squares (or anything with a known
            size), type the distance in feet. Saved per map.
          </p>
        </div>
      )}
    </Panel>
  )
}

const NUDGES = [-10, -1, -0.1, 0.1, 1, 10]

export function CalibrationControls({ state, send, defaultHexPx }: { state: TableState; send: Sync['send']; defaultHexPx: number }) {
  const cal = state.calibration
  const tv = state.tableViewport
  const [draft, setDraft] = useState(cal.hexFlatToFlatPx.toFixed(2))
  useEffect(() => setDraft(cal.hexFlatToFlatPx.toFixed(2)), [cal.hexFlatToFlatPx])
  const expected32 = expectedCssPxPerInch(32, 3840, 2160, tv.dpr)

  const commit = () => {
    const v = parseFloat(draft)
    if (Number.isFinite(v)) send({ type: 'calibration', patch: { hexFlatToFlatPx: v } })
  }

  return (
    <Panel
      title="1-Inch Calibration"
      actions={
        <Button active={cal.active} onClick={() => send({ type: 'calibration', patch: { active: !cal.active } })}>
          {cal.active ? '● Ruler on table' : 'Show ruler on table'}
        </Button>
      }
    >
      <div className="space-y-3">
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-slate-400">1" hex, flat-to-flat</span>
          <span className="font-mono text-2xl text-amber-300">{cal.hexFlatToFlatPx.toFixed(2)} px</span>
        </div>
        <div className="grid grid-cols-6 gap-1">
          {NUDGES.map((n) => (
            <Button key={n} className="px-1 font-mono text-xs" onClick={() => send({ type: 'calibration', nudge: n })}>
              {n > 0 ? '+' : ''}
              {n}
            </Button>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => e.key === 'Enter' && commit()}
            inputMode="decimal"
            className="w-full rounded-lg bg-slate-800 px-3 py-1.5 font-mono text-sm ring-1 ring-white/10 outline-none focus:ring-amber-400"
          />
          <Button onClick={() => send({ type: 'calibration', patch: { hexFlatToFlatPx: Number(expected32.toFixed(2)) } })} title="Theoretical value for a 32-inch 3840x2160 panel at the table's current scaling">
            32" 4K preset
          </Button>
          <Button variant="subtle" onClick={() => send({ type: 'calibration', patch: { hexFlatToFlatPx: defaultHexPx } })}>
            Reset
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-slate-500">
          Turn on the ruler, lay a physical ruler (or a 1" mini base) on the glass and nudge until 10 cells span exactly
          10 inches. Measured in the table browser's CSS pixels, so OS scaling (table reports{' '}
          <span className="font-mono text-slate-400">{tv.dpr}×</span>) is already included. Theory for a 32" 4K panel at
          this scaling: <span className="font-mono text-slate-400">{expected32.toFixed(2)} px</span>.
        </p>
      </div>
    </Panel>
  )
}
