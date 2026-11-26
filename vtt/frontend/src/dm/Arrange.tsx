import { createContext, useContext, useState, type ReactNode } from 'react'
import { locate, movePanel, PANEL_TITLES, WIDE, ZONE_TITLES, type Layout, type PanelId, type Zone } from './layout'

/**
 * Layout edit mode for the DM screen. While editing, panels render as compact cards that can be
 * dragged (mouse) or moved with arrow buttons (touch / keyboard); changes go to a draft that the
 * DM page saves or discards.
 */
interface ArrangeApi {
  editing: boolean
  layout: Layout
  setLayout: (l: Layout) => void
}

const ArrangeContext = createContext<ArrangeApi | null>(null)
const DragContext = createContext<{
  dragging: PanelId | null
  setDragging: (id: PanelId | null) => void
  hint: string | null
  setHint: (h: string | null) => void
} | null>(null)

export function ArrangeProvider({ value, children }: { value: ArrangeApi; children: ReactNode }) {
  const [dragging, setDragging] = useState<PanelId | null>(null)
  const [hint, setHint] = useState<string | null>(null)
  return (
    <ArrangeContext.Provider value={value}>
      <DragContext.Provider value={{ dragging, setDragging, hint, setHint }}>{children}</DragContext.Provider>
    </ArrangeContext.Provider>
  )
}

function useArrange() {
  const a = useContext(ArrangeContext)
  const d = useContext(DragContext)
  if (!a || !d) throw new Error('ArrangeProvider missing')
  return { ...a, ...d }
}

/** Rough card heights in edit mode, so the wireframe looks like the real page. */
const CARD_HEIGHT: Partial<Record<PanelId, number>> = { preview: 220, library: 130 }

export function ZoneView({ zone, className, render }: { zone: Zone; className: string; render: (id: PanelId) => ReactNode }) {
  const a = useArrange()
  const ids = a.layout[zone]
  return (
    <div className={className}>
      {ids.map((id, i) => (
        <Slot key={id} id={id} zone={zone} index={i} count={ids.length}>
          {render(id)}
        </Slot>
      ))}
      {a.editing && <EndDrop zone={zone} empty={ids.length === 0} />}
    </div>
  )
}

function Slot({ id, zone, index, count, children }: { id: PanelId; zone: Zone; index: number; count: number; children: ReactNode }) {
  const a = useArrange()
  const wide = zone === 'main' && WIDE.has(id) ? 'md:col-span-2 2xl:col-span-3' : ''
  if (!a.editing) return <div className={`min-w-0 ${wide}`}>{children}</div>

  const move = (z: Zone, i: number) => a.setLayout(movePanel(a.layout, id, z, i))
  const other: Zone = zone === 'main' ? 'side' : 'main'
  const before = a.hint === `${id}:before`
  const after = a.hint === `${id}:after`

  const dropPos = (e: React.DragEvent): 'before' | 'after' => {
    const r = e.currentTarget.getBoundingClientRect()
    // diagonal split works for both stacked and side-by-side cards
    return (e.clientX - r.left) / r.width + (e.clientY - r.top) / r.height < 1 ? 'before' : 'after'
  }

  return (
    <div
      className={`relative min-w-0 ${wide}`}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = 'move'
        e.dataTransfer.setData('text/plain', id)
        a.setDragging(id)
      }}
      onDragEnd={() => {
        a.setDragging(null)
        a.setHint(null)
      }}
      onDragOver={(e) => {
        if (!a.dragging || a.dragging === id) return
        e.preventDefault()
        const h = `${id}:${dropPos(e)}`
        if (h !== a.hint) a.setHint(h)
      }}
      onDrop={(e) => {
        e.preventDefault()
        const dragged = a.dragging
        a.setDragging(null)
        a.setHint(null)
        if (!dragged || dragged === id) return
        const from = locate(a.layout, dragged)
        let target = dropPos(e) === 'before' ? index : index + 1
        if (from.zone === zone && from.index < index) target -= 1 // list shrinks when the card is lifted
        a.setLayout(movePanel(a.layout, dragged, zone, target))
      }}
    >
      {before && <DropBar side="top" />}
      <div
        className={`flex cursor-grab flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed bg-slate-900/80 px-3 text-center transition active:cursor-grabbing ${
          a.dragging === id ? 'border-amber-300 opacity-40' : 'border-amber-400/40 hover:border-amber-300'
        }`}
        style={{ height: CARD_HEIGHT[id] ?? 104 }}
      >
        <div className="text-sm font-semibold text-amber-100">
          <span className="mr-1.5 text-amber-400">⠿</span>
          {PANEL_TITLES[id]}
        </div>
        <div className="flex flex-wrap justify-center gap-1">
          <CardButton label="↑" title="Move earlier" disabled={index === 0} onClick={() => move(zone, index - 1)} />
          <CardButton label="↓" title="Move later" disabled={index === count - 1} onClick={() => move(zone, index + 1)} />
          <CardButton
            label={zone === 'main' ? 'To sidebar →' : '← To main area'}
            title={`Move to the ${ZONE_TITLES[other]}`}
            onClick={() => move(other, a.layout[other].length)}
          />
        </div>
      </div>
      {after && <DropBar side="bottom" />}
    </div>
  )
}

function EndDrop({ zone, empty }: { zone: Zone; empty: boolean }) {
  const a = useArrange()
  const active = a.hint === `end:${zone}`
  return (
    <div
      className={`flex items-center justify-center rounded-xl border-2 border-dashed px-3 text-center text-xs transition ${
        zone === 'main' ? 'md:col-span-2 2xl:col-span-3' : ''
      } ${
        active ? 'border-amber-300 bg-amber-400/10 text-amber-200' : 'border-white/10 text-slate-500'
      }`}
      style={{ height: empty ? 120 : 48 }}
      onDragOver={(e) => {
        if (!a.dragging) return
        e.preventDefault()
        if (a.hint !== `end:${zone}`) a.setHint(`end:${zone}`)
      }}
      onDrop={(e) => {
        e.preventDefault()
        const dragged = a.dragging
        a.setDragging(null)
        a.setHint(null)
        if (dragged) a.setLayout(movePanel(a.layout, dragged, zone, a.layout[zone].length))
      }}
    >
      {empty ? `The ${ZONE_TITLES[zone]} is empty — drop panels here` : `Drop here to move to the end of the ${ZONE_TITLES[zone]}`}
    </div>
  )
}

function DropBar({ side }: { side: 'top' | 'bottom' }) {
  return <div className={`pointer-events-none absolute right-0 left-0 z-10 h-1 rounded bg-amber-400 ${side === 'top' ? '-top-2.5' : '-bottom-2.5'}`} />
}

function CardButton({ label, title, onClick, disabled }: { label: string; title: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      onMouseDown={(e) => e.stopPropagation()}
      className="rounded-md bg-white/10 px-2.5 py-1 text-xs font-medium text-slate-200 hover:bg-amber-500 hover:text-slate-950 disabled:opacity-30 disabled:hover:bg-white/10 disabled:hover:text-slate-200"
    >
      {label}
    </button>
  )
}
