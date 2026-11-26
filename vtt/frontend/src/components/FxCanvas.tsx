import { useEffect, useRef } from 'react'
import { AmbientFx } from '../lib/fx'
import type { FxSettings } from '../lib/types'

/** Layer 2: procedural ambient effects (fog, rain, embers, torchlight). */
export function FxCanvas({ fx, width, height }: { fx: FxSettings; width: number; height: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const engine = useRef<AmbientFx | null>(null)

  useEffect(() => {
    engine.current = new AmbientFx(ref.current!)
    return () => engine.current?.stop()
  }, [])

  useEffect(() => {
    if (width > 0 && height > 0) engine.current?.resize(width, height)
  }, [width, height])

  useEffect(() => {
    engine.current?.update(fx)
  }, [fx])

  return <canvas ref={ref} className="layer" />
}
