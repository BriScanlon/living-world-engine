import { useEffect, useState, type RefObject } from 'react'

/** CSS-pixel size of an element, kept current with ResizeObserver. */
export function useElementSize(ref: RefObject<HTMLElement | null>) {
  const [size, setSize] = useState({ w: 0, h: 0 })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setSize({ w: el.clientWidth, h: el.clientHeight })
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return size
}

export function useDevicePixelRatio() {
  const [dpr, setDpr] = useState(window.devicePixelRatio || 1)
  useEffect(() => {
    let mq: MediaQueryList | null = null
    const listen = () => {
      mq?.removeEventListener('change', onChange)
      mq = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`)
      mq.addEventListener('change', onChange)
    }
    const onChange = () => {
      setDpr(window.devicePixelRatio || 1)
      listen()
    }
    listen()
    return () => mq?.removeEventListener('change', onChange)
  }, [])
  return dpr
}
