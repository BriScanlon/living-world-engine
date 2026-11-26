import { useEffect, useRef } from 'react'
import type { Rect } from '../lib/mapView'
import type { Asset } from '../lib/types'

interface Props {
  map: Asset
  /** Where the image goes inside its container, in that container's CSS px. */
  rect: Rect
  playbackRate: number
  /** DM preview: prefer the light still frame over decoding a 4K video. */
  preferStill?: boolean
  /** Reports the media's pixel size once loaded (needed when the DB has none). */
  onNaturalSize?: (w: number, h: number) => void
}

/** Layer 1: seamless looping video or static 4K image, placed by the map view. */
export function MapMedia({ map, rect, playbackRate, preferStill, onNaturalSize }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const isVideo = map.media_type === 'video' && !(preferStill && (map.still_url || map.thumb_url))

  useEffect(() => {
    const v = videoRef.current
    if (v) v.playbackRate = playbackRate
  }, [playbackRate, map.id])

  const style = {
    position: 'absolute',
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    maxWidth: 'none',
    objectFit: 'fill',
    pointerEvents: 'none',
  } as const

  if (isVideo) {
    return (
      <video
        key={map.id}
        ref={videoRef}
        className="fade-in"
        style={style}
        src={map.media_url}
        poster={map.still_url ?? undefined}
        autoPlay
        loop
        muted
        playsInline
        disablePictureInPicture
        onLoadedMetadata={(e) => onNaturalSize?.(e.currentTarget.videoWidth, e.currentTarget.videoHeight)}
        onLoadedData={(e) => {
          e.currentTarget.playbackRate = playbackRate
          void e.currentTarget.play().catch(() => undefined)
        }}
      />
    )
  }
  const src = preferStill ? map.still_url ?? map.media_url : map.media_url
  return (
    <img
      key={map.id}
      className="fade-in"
      style={style}
      src={src}
      alt=""
      draggable={false}
      decoding="async"
      onLoad={(e) => onNaturalSize?.(e.currentTarget.naturalWidth, e.currentTarget.naturalHeight)}
    />
  )
}
