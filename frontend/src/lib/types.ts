export type GridType = 'hex-pointy' | 'hex-flat' | 'square'

export interface GridSettings {
  visible: boolean
  type: GridType
  color: string
  opacity: number
  /** Stroke width in device pixels. */
  lineWidth: number
  showCoords: boolean
  /** Grid origin shift in table CSS px (to line the grid up with painted map features). */
  offsetX: number
  offsetY: number
}

export interface FxToggle {
  enabled: boolean
  intensity: number
}

export interface FxSettings {
  fog: FxToggle
  rain: FxToggle
  embers: FxToggle
  torch: FxToggle
}
export type FxName = keyof FxSettings

export type FogMode = 'reveal' | 'hide'
export type FogOp =
  | { mode: FogMode; shape: 'circle'; x: number; y: number; r: number }
  | { mode: FogMode; shape: 'poly'; pts: [number, number][] }

export interface FogState {
  enabled: boolean
  base: 'hidden' | 'revealed'
  ops: FogOp[]
}

export interface Asset {
  id: string
  asset_type: 'map' | 'token'
  title: string
  source: string
  media_type: 'image' | 'video'
  media_url: string
  still_url: string | null
  thumb_url: string | null
  width: number | null
  height: number | null
  description: string | null
  style: string | null
  image_prompt: string | null
  motion_prompt: string | null
  pack: string | null
  tags: string[]
  mock: boolean
  created_at: number
}

export interface DisplaySettings {
  playbackRate: number
}

/** Per-map placement on the table (stored per map on the server). */
export interface MapView {
  /** cover = fill screen, contain = letterbox, scale = true 1" = 5 ft */
  mode: 'cover' | 'contain' | 'scale'
  /** Image pixels that make one 5 ft square (used in 'scale' mode). */
  pxPerSquare: number
  /** Map point (0..1 of the image) shown at the centre of the table. */
  cx: number
  cy: number
}

export interface Calibration {
  /** Physical 1" hex measured flat-to-flat, in table CSS pixels. */
  hexFlatToFlatPx: number
  active: boolean
}

export interface TableViewport {
  w: number
  h: number
  dpr: number
}

export interface TableState {
  map: Asset | null
  display: DisplaySettings
  view: MapView
  grid: GridSettings
  calibration: Calibration
  fx: FxSettings
  fog: FogState
  tableViewport: TableViewport
  clients: { table: number; dm: number }
}

export interface Job {
  id: string
  kind: 'generate' | 'animate'
  status: 'queued' | 'running' | 'done' | 'error'
  stage: 'queued' | 'prompting' | 'base_map' | 'animating' | 'saving' | 'done' | 'error'
  stage_label?: string
  progress: number
  message: string
  error: string | null
  input: Record<string, unknown>
  result_asset_id: string | null
  created_at: number
  updated_at: number
}

export interface AppConfig {
  version: string
  mock: boolean
  styles: string[]
  animationEnabled: boolean
  watchInbox: boolean
  defaultHexPx: number
}
