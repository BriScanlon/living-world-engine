import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FogStore } from './fog'
import type { Asset, FogOp, Job, Scene, TableState } from './types'

export type Role = 'table' | 'dm'

export interface Toast {
  id: number
  level: 'info' | 'warn' | 'error' | 'success'
  message: string
}

type PartKey = 'grid' | 'display' | 'fx' | 'calibration' | 'view'
type DeepPartial<T> = { [P in keyof T]?: T[P] extends object ? DeepPartial<T[P]> : T[P] }

/**
 * Live connection to the VTT server. Keeps TableState in React state, fog in a
 * FogStore (outside React, incremental), and the DM's job list.
 */
export function useTableSync(role: Role) {
  const [state, setState] = useState<TableState | null>(null)
  const [connected, setConnected] = useState(false)
  const [jobs, setJobs] = useState<Job[]>([])
  const [libraryVersion, setLibraryVersion] = useState(0)
  const [scene, setScene] = useState<Scene | null>(null)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [fogMeta, setFogMeta] = useState({ enabled: false, base: 'hidden' as 'hidden' | 'revealed', count: 0 })
  const fog = useMemo(() => new FogStore(), [])
  const wsRef = useRef<WebSocket | null>(null)

  const toast = useCallback((message: string, level: Toast['level'] = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t.slice(-3), { id, level, message }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000)
  }, [])

  useEffect(() => {
    let closed = false
    let retry = 0
    let ping: number | undefined
    let reconnectTimer: number | undefined

    const syncFogMeta = () => setFogMeta({ enabled: fog.enabled, base: fog.base, count: fog.ops.length })

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws?role=${role}`)
      wsRef.current = ws
      ws.onopen = () => {
        retry = 0
        setConnected(true)
        ping = window.setInterval(() => ws.readyState === 1 && ws.send('{"type":"ping"}'), 20000)
      }
      ws.onclose = () => {
        setConnected(false)
        window.clearInterval(ping)
        if (!closed) reconnectTimer = window.setTimeout(connect, Math.min(5000, 500 * 2 ** retry++))
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (ev) => {
        let msg: any
        try {
          msg = JSON.parse(ev.data)
        } catch {
          return
        }
        switch (msg.type) {
          case 'state':
            setState(msg.state)
            fog.setFull(msg.state.fog)
            syncFogMeta()
            break
          case 'state_part':
            setState((s) => (s ? { ...s, [msg.key]: msg.value } : s))
            break
          case 'fog_full':
            fog.setFull(msg.fog)
            syncFogMeta()
            break
          case 'fog_op':
            fog.addOp(msg.op)
            syncFogMeta()
            break
          case 'fog_meta':
            fog.enabled = !!msg.enabled
            syncFogMeta()
            break
          case 'jobs':
            setJobs(msg.jobs)
            break
          case 'job':
            setJobs((js) => {
              const i = js.findIndex((j) => j.id === msg.job.id)
              if (i < 0) return [msg.job, ...js].slice(0, 30)
              const copy = js.slice()
              copy[i] = msg.job
              return copy
            })
            if (msg.job.status === 'error') toast(`Job failed: ${msg.job.error}`, 'error')
            break
          case 'asset_added':
            setLibraryVersion((v) => v + 1)
            toast(`Added “${(msg.asset as Asset).title}” to the library`, 'success')
            break
          case 'library_changed':
            setLibraryVersion((v) => v + 1)
            break
          case 'toast':
            toast(msg.message, msg.level ?? 'info')
            break
          case 'scene':
            setScene(msg.scene ?? null)
            if (msg.scene?.place?.name) toast(`World: ${msg.scene.place.name}`, 'info')
            break
        }
      }
    }
    connect()
    return () => {
      closed = true
      window.clearInterval(ping)
      window.clearTimeout(reconnectTimer)
      wsRef.current?.close()
    }
  }, [role, fog, toast])

  const send = useCallback((msg: unknown) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
  }, [])

  /** Optimistically patch a settings block locally and send it to the server. */
  const patch = useCallback(
    <K extends PartKey>(key: K, value: DeepPartial<TableState[K]>) => {
      setState((s) => (s ? { ...s, [key]: deepMerge(s[key], value) } : s))
      send({ type: key, patch: value })
    },
    [send],
  )

  /** DM draws locally at once; the server relays to everyone else. */
  const sendFogOp = useCallback(
    (op: FogOp) => {
      fog.addOp(op)
      setFogMeta((m) => ({ ...m, count: fog.ops.length }))
      send({ type: 'fog_op', op })
    },
    [fog, send],
  )

  return { state, connected, send, patch, fog, fogMeta, sendFogOp, jobs, libraryVersion, scene, toasts, toast }
}

function deepMerge<T>(base: T, patch: any): T {
  if (typeof base !== 'object' || base === null || typeof patch !== 'object' || patch === null) return patch ?? base
  const out: any = Array.isArray(base) ? base.slice() : { ...base }
  for (const k of Object.keys(patch)) out[k] = deepMerge((base as any)[k], patch[k])
  return out
}
