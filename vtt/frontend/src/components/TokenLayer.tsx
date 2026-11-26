import type { Rect, Size } from '../lib/mapView'
import type { Token } from '../lib/types'

interface Props {
  tokens: Token[]
  /** The map's rectangle in container CSS px; tokens are anchored to the map like fog. */
  rect: Rect
  natural: Size | null
  /** Image px per 5 ft square for this map (its view's pxPerSquare). */
  pxPerSquare: number
  /** DM preview: names under tokens, hidden tokens shown faded, tokens can be grabbed. */
  dm?: boolean
  interactive?: boolean
  selectedId?: string | null
  onTokenPointerDown?: (token: Token, e: React.PointerEvent<HTMLDivElement>) => void
  onTokenPointerMove?: (e: React.PointerEvent<HTMLDivElement>) => void
  onTokenPointerUp?: (e: React.PointerEvent<HTMLDivElement>) => void
}

function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  return (parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : name.slice(0, 2)).toUpperCase()
}

/** Tokens on the map: portraits (or coloured discs) sized in 5 ft squares. Sits above the grid, below the fog. */
export function TokenLayer({
  tokens, rect, natural, pxPerSquare, dm = false, interactive = false, selectedId,
  onTokenPointerDown, onTokenPointerMove, onTokenPointerUp,
}: Props) {
  if (!natural || natural.w <= 0 || !tokens.length) return null
  const cssPerImagePx = rect.width / natural.w
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {tokens.map((t) => {
        const d = Math.max(6, t.size * pxPerSquare * cssPerImagePx)
        const selected = dm && t.id === selectedId
        return (
          <div
            key={t.id}
            className={`absolute select-none ${interactive ? 'pointer-events-auto cursor-grab touch-none active:cursor-grabbing' : ''}`}
            style={{
              left: rect.left + t.x * rect.width - d / 2,
              top: rect.top + t.y * rect.height - d / 2,
              width: d,
              height: d,
              opacity: dm && t.hidden ? 0.45 : 1,
            }}
            onPointerDown={interactive ? (e) => onTokenPointerDown?.(t, e) : undefined}
            onPointerMove={interactive ? onTokenPointerMove : undefined}
            onPointerUp={interactive ? onTokenPointerUp : undefined}
            onPointerCancel={interactive ? onTokenPointerUp : undefined}
            title={dm ? `${t.name}${t.hidden ? ' (hidden from players)' : ''}` : undefined}
          >
            <div
              className="h-full w-full overflow-hidden rounded-full"
              style={{
                boxShadow: `0 0 0 ${Math.max(1.5, d * 0.05)}px ${t.color}, 0 2px 8px rgba(0,0,0,0.7)`,
                outline: selected || (dm && t.hidden)
                  ? `2px ${dm && t.hidden ? 'dashed' : 'solid'} ${selected ? '#fde68a' : '#94a3b8'}`
                  : undefined,
                outlineOffset: selected || (dm && t.hidden) ? Math.max(2, d * 0.06) : undefined,
                background: t.img ? '#0f172a' : t.color,
              }}
            >
              {t.img ? (
                <img src={t.img} alt="" draggable={false} className="h-full w-full object-cover" />
              ) : (
                <div
                  className="flex h-full w-full items-center justify-center font-bold text-slate-950"
                  style={{ fontSize: Math.max(7, d * 0.36) }}
                >
                  {initials(t.name)}
                </div>
              )}
            </div>
            {dm && d >= 14 && (
              <div className="absolute top-full left-1/2 mt-0.5 -translate-x-1/2 rounded bg-black/70 px-1 text-[10px] leading-tight whitespace-nowrap text-slate-100">
                {t.hidden ? '◌ ' : ''}{t.name}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
