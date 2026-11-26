/**
 * DM panel layout: which panels sit in the wide main area vs the sidebar, and in what order.
 * Saved per browser (localStorage), so a laptop and a tablet can each have their own layout.
 */

export type PanelId =
  | 'preview'
  | 'mapScale'
  | 'grid'
  | 'calibration'
  | 'fx'
  | 'display'
  | 'library'
  | 'generator'
  | 'jobs'
  | 'importer'

export type Zone = 'main' | 'side'
export type Layout = Record<Zone, PanelId[]>

export const PANEL_TITLES: Record<PanelId, string> = {
  preview: 'Table Preview',
  mapScale: 'Map Scale',
  grid: 'Grid',
  calibration: '1-Inch Calibration',
  fx: 'Ambient FX',
  display: 'Display',
  library: 'Map Library',
  generator: 'Map Generator',
  jobs: 'AI Jobs',
  importer: 'Import Static Map',
}

export const ZONE_TITLES: Record<Zone, string> = { main: 'main area', side: 'sidebar' }

export const DEFAULT_LAYOUT: Layout = {
  main: ['preview', 'mapScale', 'grid', 'calibration', 'fx', 'display', 'library'],
  side: ['generator', 'jobs', 'importer'],
}

/** Panels that take the full width of the main area's grid. */
export const WIDE = new Set<PanelId>(['preview', 'library'])

const KEY = 'vtt.dm.layout.v1'
const ALL = Object.keys(PANEL_TITLES) as PanelId[]

/** Keep every known panel exactly once; anything missing goes back to its default zone. */
export function normalize(input: unknown): Layout {
  const out: Layout = { main: [], side: [] }
  const seen = new Set<PanelId>()
  const src = (input && typeof input === 'object' ? input : {}) as Partial<Record<Zone, unknown>>
  for (const zone of ['main', 'side'] as Zone[]) {
    const list = Array.isArray(src[zone]) ? (src[zone] as unknown[]) : []
    for (const id of list) {
      if (typeof id === 'string' && (ALL as string[]).includes(id) && !seen.has(id as PanelId)) {
        out[zone].push(id as PanelId)
        seen.add(id as PanelId)
      }
    }
  }
  for (const zone of ['main', 'side'] as Zone[])
    for (const id of DEFAULT_LAYOUT[zone]) if (!seen.has(id)) out[zone].push(id)
  return out
}

export function loadLayout(): Layout {
  try {
    const raw = localStorage.getItem(KEY)
    return normalize(raw ? JSON.parse(raw) : DEFAULT_LAYOUT)
  } catch {
    return normalize(DEFAULT_LAYOUT)
  }
}

export function saveLayout(layout: Layout) {
  try {
    localStorage.setItem(KEY, JSON.stringify(layout))
  } catch {
    /* private mode / storage blocked: layout just won't persist */
  }
}

export function sameLayout(a: Layout, b: Layout) {
  return a.main.join() === b.main.join() && a.side.join() === b.side.join()
}

/** Move `id` so that it ends up at `index` within `zone` (index counted after removal). */
export function movePanel(layout: Layout, id: PanelId, zone: Zone, index: number): Layout {
  const next: Layout = { main: layout.main.filter((p) => p !== id), side: layout.side.filter((p) => p !== id) }
  const i = Math.max(0, Math.min(index, next[zone].length))
  next[zone].splice(i, 0, id)
  return next
}

export function locate(layout: Layout, id: PanelId): { zone: Zone; index: number } {
  const zone: Zone = layout.main.includes(id) ? 'main' : 'side'
  return { zone, index: layout[zone].indexOf(id) }
}
