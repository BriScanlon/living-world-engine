import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { GeoJSONSource, Map as MlMap, Marker } from "maplibre-gl";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, AtlasState, Branch, MapBrief, PaintStatus, PlaceDetail, VttMap } from "../api";
import { accuracyTone, Badge, Button, Card, cx, Empty, ErrorBox, Field, inputCls, Spinner } from "../ui";

let protocolAdded = false;
function ensurePmtiles() {
  if (protocolAdded) return;
  maplibregl.addProtocol("pmtiles", new Protocol().tile);
  protocolAdded = true;
}

const EVENT_COLORS: [string, string][] = [
  ["party_action", "#f0ad4e"],
  ["conflict", "#f43f5e"],
  ["death", "#f43f5e"],
  ["faction_move", "#a78bfa"],
  ["goal_achieved", "#34d399"],
  ["goal_progress", "#7d8796"],
];

export default function Atlas({ branches }: { branches: Branch[] }) {
  const mapEl = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const markers = useRef<Marker[]>([]);
  const loadedSlug = useRef<string | null>(null);
  const openRef = useRef<(id: string) => void>(() => undefined);
  const footprintIds = useRef<string[]>([]);
  const [ready, setReady] = useState(false);
  const [branch, setBranch] = useState("main");
  const [compare, setCompare] = useState(true);
  const [day, setDay] = useState<number | null>(null);
  const [state, setState] = useState<AtlasState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<PlaceDetail | null>(null);
  const [playing, setPlaying] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const compareTo = branch !== "main" && compare ? "main" : null;

  // ------------------------------------------------------------------ data
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await api.atlas(branch, day, compareTo);
      setState(s);
      if (day === null) setDay(s.day);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [branch, day, compareTo]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!playing || !state) return;
    if ((day ?? 0) >= state.max_day) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setDay((d) => (d ?? 0) + 1), 700);
    return () => clearTimeout(t);
  }, [playing, day, state]);

  // ------------------------------------------------------------------ map init
  useEffect(() => {
    if (!mapEl.current || mapRef.current) return;
    ensurePmtiles();
    const map = new maplibregl.Map({
      container: mapEl.current,
      style: {
        version: 8,
        projection: { type: "globe" },
        sources: {},
        layers: [{ id: "space", type: "background", paint: { "background-color": "#16324f" } }],
      },
      center: [0, 15],
      zoom: 1.4,
      attributionControl: { compact: true, customAttribution: "World: Azgaar FMG format · MapLibre" },
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.on("load", () => setReady(true));
    mapRef.current = map;
    if (import.meta.env.DEV) (window as unknown as { __atlasMap: MlMap }).__atlasMap = map; // for debugging
    return () => {
      map.remove();
      mapRef.current = null;
      loadedSlug.current = null; // a new map needs its layers added again
      footprintIds.current = [];
      setReady(false);
    };
  }, []);

  // ------------------------------------------------------------------ static layers (once per atlas)
  useEffect(() => {
    const map = mapRef.current;
    const a = state?.atlas;
    if (!map || !ready || !a || loadedSlug.current === a.slug) return;
    loadedSlug.current = a.slug;
    const tiled = a.has_tiles;
    const src = (layer: string) => (tiled ? "base" : `l-${layer}`);
    const srcLayer = (layer: string) => (tiled ? { "source-layer": layer } : {});
    if (tiled) {
      map.addSource("base", {
        type: "vector",
        url: `pmtiles://${location.origin}${api.atlasFile(a.slug, "tiles.pmtiles")}`,
        promoteId: { provinces: "pid" },
      });
    } else {
      for (const l of a.layers) {
        map.addSource(`l-${l}`, {
          type: "geojson",
          // absolute: MapLibre fetches GeoJSON in a web worker, where relative URLs don't resolve
          data: `${location.origin}${api.atlasFile(a.slug, `${l}.geojson`)}`,
          ...(l === "provinces" ? { promoteId: "pid" } : {}),
        });
      }
    }
    map.addLayer({ id: "land", type: "fill", source: src("land"), ...srcLayer("land"), paint: { "fill-color": "#cdbf95" } });
    map.addLayer({
      id: "biomes", type: "fill", source: src("biomes"), ...srcLayer("biomes"),
      paint: { "fill-color": ["get", "color"], "fill-opacity": 0.75 },
    });
    map.addLayer({
      id: "province-fill", type: "fill", source: src("provinces"), ...srcLayer("provinces"),
      paint: { "fill-color": ["coalesce", ["feature-state", "color"], "rgba(0,0,0,0)"], "fill-opacity": 0.5 },
    });
    map.addLayer({
      id: "province-line", type: "line", source: src("provinces"), ...srcLayer("provinces"),
      paint: { "line-color": "#1b1f24", "line-width": 0.7, "line-opacity": 0.7 },
    });
    map.addLayer({
      id: "province-changed", type: "line", source: src("provinces"), ...srcLayer("provinces"),
      paint: {
        "line-color": "#f5c77e",
        "line-width": ["case", ["boolean", ["feature-state", "changed"], false], 3, 0],
        "line-dasharray": [2, 1],
      },
    });
    map.addLayer({
      id: "rivers", type: "line", source: src("rivers"), ...srcLayer("rivers"),
      paint: { "line-color": "#5b9bd5", "line-width": ["interpolate", ["linear"], ["zoom"], 1, 0.6, 7, 2.2] },
    });
    map.addLayer({
      id: "roads", type: "line", source: src("roads"), ...srcLayer("roads"), minzoom: 3,
      paint: { "line-color": "#6b4f2a", "line-width": 1, "line-dasharray": [3, 2], "line-opacity": 0.8 },
    });
    // live layers
    const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
    map.addSource("places", { type: "geojson", data: empty });
    map.addSource("events", { type: "geojson", data: empty });
    map.addSource("rumors", { type: "geojson", data: empty });
    map.addSource("footprints", { type: "geojson", data: empty });
    map.addLayer({
      id: "footprint-line", type: "line", source: "footprints", minzoom: 11,
      paint: { "line-color": "#f5c77e", "line-width": 1.5, "line-dasharray": [2, 1] },
    });
    map.addLayer({
      id: "places", type: "circle", source: "places",
      paint: {
        "circle-radius": ["match", ["get", "kind"], "capital", 5, "city", 4, 3],
        "circle-color": ["case", ["get", "campaign"], "#f0ad4e", "#e6eaf0"],
        "circle-stroke-color": "#0d0f12", "circle-stroke-width": 1.2,
      },
    });
    map.addLayer({
      id: "events", type: "circle", source: "events",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["get", "age"], 0, 9, 30, 4],
        "circle-color": ["match", ["get", "event_type"], ...EVENT_COLORS.flat(), "#e6eaf0"] as any,
        "circle-opacity": 0.35,
        "circle-stroke-color": ["case", ["get", "new"], "#f5c77e", "#e6eaf0"],
        "circle-stroke-width": ["case", ["get", "new"], 2, 1],
      },
    });
    map.addLayer({
      id: "rumors", type: "circle", source: "rumors",
      paint: { "circle-radius": 3, "circle-color": "#a78bfa", "circle-stroke-color": "#0d0f12", "circle-stroke-width": 1, "circle-translate": [8, -8] },
    });
    const pick = (layer: string) => (e: maplibregl.MapLayerMouseEvent) => {
      const f = e.features?.[0];
      if (!f) return;
      const id = layer === "province-fill" ? `Province/${f.id ?? f.properties?.pid}` : f.properties?.id;
      if (id && layer !== "events") openRef.current(id);
      if (layer === "events") {
        new maplibregl.Popup({ closeButton: false, maxWidth: "280px" })
          .setLngLat(e.lngLat)
          .setHTML(`<div style="color:#14171c"><b>Day ${f.properties?.day}: ${escape(f.properties?.title)}</b><br/>${escape(f.properties?.summary ?? "")}</div>`)
          .addTo(map);
      }
    };
    for (const l of ["province-fill", "places", "events"]) {
      map.on("click", l, pick(l));
      map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
    }
    if (a.centre) map.flyTo({ center: a.centre, zoom: 2.2, speed: 0.6 });
  }, [ready, state]);

  // ------------------------------------------------------------------ live layers on every state change
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !state?.atlas || loadedSlug.current !== state.atlas.slug || !map.getSource("places")) return;
    const tiled = state.atlas.has_tiles;
    for (const [uid, p] of Object.entries(state.provinces)) {
      map.setFeatureState(
        tiled ? { source: "base", sourceLayer: "provinces", id: uid } : { source: "l-provinces", id: uid },
        { color: p.color, changed: p.changed },
      );
    }
    (map.getSource("places") as GeoJSONSource).setData(state.places);
    const evs = showSecret ? state.events : { ...state.events, features: state.events.features.filter((f) => f.properties?.visibility !== "secret") };
    (map.getSource("events") as GeoJSONSource).setData(evs);
    (map.getSource("rumors") as GeoJSONSource).setData(state.rumors);
    // Linked VTT battle maps, drawn at true scale (visible once zoomed into a town)
    const fps = state.maps ?? [];
    (map.getSource("footprints") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: fps.map((f) => ({ type: "Feature", properties: { id: f.id, name: f.name },
        geometry: { type: "Polygon", coordinates: [[...f.corners, f.corners[0]]] } })),
    });
    for (const old of footprintIds.current) {
      if (!fps.some((f) => `fp-${f.id}` === old)) {
        if (map.getLayer(old)) map.removeLayer(old);
        if (map.getSource(old)) map.removeSource(old);
      }
    }
    footprintIds.current = [];
    for (const f of fps) {
      if (!f.thumb_path) continue;
      const sid = `fp-${f.id}`;
      footprintIds.current.push(sid);
      if (map.getSource(sid)) continue;
      map.addSource(sid, { type: "image", url: `${location.origin}/api/vtt/media/${f.thumb_path}`,
        coordinates: f.corners as [[number, number], [number, number], [number, number], [number, number]] });
      map.addLayer({ id: sid, type: "raster", source: sid, minzoom: 11, paint: { "raster-opacity": 0.92 } }, "footprint-line");
    }
    // HTML labels (no glyph server needed): capitals and the campaign's own places
    markers.current.forEach((m) => m.remove());
    markers.current = state.places.features
      .filter((f) => ["capital", "continent"].includes(f.properties?.kind) || f.properties?.start)
      .map((f) => {
        const el = document.createElement("div");
        el.textContent = f.properties?.name;
        el.className = "atlas-label";
        const continent = f.properties?.kind === "continent";
        el.style.cssText = continent
          ? "font:italic 600 14px Georgia,serif;letter-spacing:.12em;text-transform:uppercase;color:#f3ead2;opacity:.85;text-shadow:0 0 4px #000;pointer-events:none;white-space:nowrap"
          : `font:600 11px system-ui;color:${f.properties?.start ? "#f5c77e" : "#e6eaf0"};text-shadow:0 0 3px #000,0 0 2px #000;pointer-events:none;transform:translateY(-12px);white-space:nowrap`;
        return new maplibregl.Marker({ element: el, anchor: "bottom" })
          .setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
          .addTo(map);
      });
  }, [state, showSecret]);

  const openDetail = useCallback(
    (id: string) => {
      api.place(id, branch, day).then(setDetail).catch((e) => setError(e.message));
    },
    [branch, day],
  );
  openRef.current = openDetail;

  const changed = useMemo(() => Object.values(state?.provinces ?? {}).filter((p) => p.changed), [state]);
  const recent = useMemo(
    () => [...(state?.events.features ?? [])]
      .filter((f) => showSecret || f.properties?.visibility !== "secret")
      .sort((a, b) => (b.properties?.day ?? 0) - (a.properties?.day ?? 0))
      .slice(0, 14),
    [state, showSecret],
  );

  if (state && !state.atlas) {
    return <Empty>No atlas on this branch yet. Import an Azgaar “Full JSON” export, or enable ATLAS_SAMPLE_ON_STARTUP.</Empty>;
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="grid items-end gap-4 md:grid-cols-[1fr_2fr_auto]">
          <Field label="Branch">
            <select className={inputCls} value={branch} onChange={(e) => { setBranch(e.target.value); setDay(null); setDetail(null); }}>
              {branches.map((b) => <option key={b.id} value={b.name}>{b.name}</option>)}
            </select>
          </Field>
          <Field label={`In-world day: ${day ?? "…"}${state ? ` of ${state.max_day}` : ""}`}>
            <div className="flex items-center gap-2">
              <Button variant="ghost" onClick={() => { if (!playing && state && (day ?? 0) >= state.max_day) setDay(0); setPlaying(!playing); }} title="Play through the days">
                {playing ? "❚❚" : "▶"}
              </Button>
              <input type="range" min={0} max={state?.max_day ?? 30} value={day ?? 0} onChange={(e) => setDay(+e.target.value)} className="w-full accent-amber-500" />
            </div>
          </Field>
          <div className="flex flex-wrap items-center gap-3 pb-1 text-sm text-ink-300">
            <label className="flex items-center gap-1.5" title="Highlight what differs from canon">
              <input type="checkbox" className="accent-amber-500" disabled={branch === "main"} checked={compare && branch !== "main"} onChange={(e) => setCompare(e.target.checked)} />
              Compare with main
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" className="accent-amber-500" checked={showSecret} onChange={(e) => setShowSecret(e.target.checked)} />
              Secret events
            </label>
            {loading && <Spinner />}
          </div>
        </div>
        <div className="mt-2"><ErrorBox error={error} /></div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(300px,1fr)]">
        <div className="relative overflow-hidden rounded-xl border border-ink-700/70">
          <div ref={mapEl} className="h-[70vh] min-h-[480px] w-full" />
          {state?.atlas && (
            <div className="pointer-events-none absolute left-3 top-3 rounded-lg bg-ink-950/80 px-3 py-2 text-xs text-ink-200">
              <div className="font-display text-sm text-ink-100">{state.atlas.name}</div>
              <div>Day {state.day} · {state.branch}{compareTo ? " vs main" : ""}</div>
            </div>
          )}
        </div>

        <div className="space-y-4">
          {detail ? (
            <Card title={detail.name} subtitle={[detail.type, detail.kind, detail.population ? `pop. ${detail.population.toLocaleString()}` : null].filter(Boolean).join(" · ")}
              actions={<Button variant="ghost" onClick={() => setDetail(null)}>✕</Button>}>
              {detail.description && <p className="text-sm text-ink-300">{detail.description}</p>}
              <div className="mt-2 space-y-1 text-sm">
                {detail.controller && <div><span className="text-ink-400">Held by</span> <span className="text-ink-100">{detail.controller.name}</span></div>}
                {detail.province && <div><span className="text-ink-400">Province</span> <button className="text-ember-300 hover:underline" onClick={() => openDetail(detail.province!.id)}>{detail.province.name}</button></div>}
              </div>
              {!!detail.people.length && <Section title="Who's here">{detail.people.map((p) => (
                <li key={p.id}>{p.name} <span className="text-ink-400">{p.role}{p.species ? ` · ${p.species}` : ""}{p.char_class ? ` ${p.char_class}` : ""}{p.srd_ref ? ` · SRD ${p.srd_ref.replace("srd:", "")}` : ""}</span></li>
              ))}</Section>}
              {!!detail.monsters?.length && <Section title="Creatures (SRD)">{detail.monsters.map((m) => (
                <li key={m.id}>{m.name} <span className="text-ink-400">CR {m.challenge}</span></li>
              ))}</Section>}
              {!!detail.places.length && <Section title="Places">{detail.places.map((p) => <li key={p.id}><button className="text-ember-300 hover:underline" onClick={() => openDetail(p.id)}>{p.name}</button> <span className="text-ink-400">{p.kind}</span></li>)}</Section>}
              {!!detail.events.length && <Section title="What happened">{detail.events.map((e) => <li key={e.id}><span className="font-mono text-xs text-ink-400">d{e.day}</span> {e.title}</li>)}</Section>}
              {detail.type === "Location" && (
                <TableLink detail={detail} branch={branch} day={day}
                  onChanged={() => { openDetail(detail.id); load(); }} />
              )}
              {!!detail.rumors.length && <Section title="Rumors heard here">{detail.rumors.map((r, i) => <li key={i} className="italic">{r.dialogue} <Badge tone={accuracyTone(r.accuracy)}>{r.accuracy}</Badge></li>)}</Section>}
            </Card>
          ) : (
            <Card title="Realms" subtitle="Click a province, town or event pin on the map.">
              <ul className="scroll-thin max-h-72 space-y-1.5 overflow-auto text-sm">
                {state?.factions.filter((f) => f.provinces > 0).map((f) => (
                  <li key={f.id} className="flex items-center gap-2">
                    <span className="h-3 w-3 shrink-0 rounded-sm" style={{ background: f.color }} />
                    <span className="truncate text-ink-100">{f.name}</span>
                    <span className="ml-auto text-xs text-ink-400">{f.provinces} prov.</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {compareTo && (
            <Card title={`Borders changed vs main (${changed.length})`}>
              {!changed.length ? <p className="text-sm text-ink-400">No province has changed hands by day {state?.day}.</p> : (
                <ul className="space-y-1.5 text-sm">
                  {changed.map((p) => (
                    <li key={p.id}>
                      <button
                        className="text-ember-300 hover:underline"
                        onClick={() => {
                          openDetail(p.id);
                          if (p.lon !== null && p.lat !== null) mapRef.current?.flyTo({ center: [p.lon, p.lat], zoom: 4, speed: 0.8 });
                        }}
                      >
                        {p.name}
                      </button>
                      <div className="text-xs text-ink-400">{p.previous} → <span className="text-ink-200">{p.controller_name}</span></div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}

          <Card title="Recent events" subtitle="Last 30 in-world days; ringed pins are new on this branch.">
            <ul className="scroll-thin max-h-80 space-y-2 overflow-auto text-sm">
              {recent.map((f) => (
                <li key={f.properties?.id} className="flex gap-2">
                  <span className="w-8 shrink-0 font-mono text-xs text-ink-400">d{f.properties?.day}</span>
                  <button
                    className={cx("text-left hover:text-ember-300", f.properties?.new ? "text-ink-100" : "text-ink-300")}
                    onClick={() => mapRef.current?.flyTo({ center: (f.geometry as GeoJSON.Point).coordinates as [number, number], zoom: 5, speed: 0.8 })}
                  >
                    {f.properties?.title}
                  </button>
                </li>
              ))}
              {!recent.length && <li className="text-ink-400">Nothing in the last 30 days.</li>}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-3">
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-400">{title}</div>
      <ul className="space-y-1 text-sm text-ink-200">{children}</ul>
    </div>
  );
}

function escape(s: string) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}


/** Link a VTT battle map to this place and put it on the table (with the scene on the VTT's DM screen). */
function TableLink({ detail, branch, day, onChanged }: { detail: PlaceDetail; branch: string; day: number | null; onChanged: () => void }) {
  const [status, setStatus] = useState<{ configured: boolean; reachable: boolean; error?: string } | null>(null);
  const [library, setLibrary] = useState<VttMap[] | null>(null);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.vttStatus().then(setStatus).catch(() => setStatus({ configured: false, reachable: false }));
  }, []);

  const run = async (label: string, fn: () => Promise<string | void>) => {
    setBusy(label); setErr(null); setNote(null);
    try {
      const msg = await fn();
      if (msg) setNote(msg);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const maps = detail.maps ?? [];
  if (status && !status.reachable) {
    return (
      <Section title="On the table (VTT)">
        <li className="text-xs text-ink-400">{status.configured ? `The VTT isn't reachable: ${status.error ?? ""}` : "No VTT linked. Set VTT_URL (docker compose does this for you)."}</li>
      </Section>
    );
  }
  return (
    <div className="mt-3">
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-400">On the table (VTT)</div>
      <ul className="space-y-2">
        {maps.map((m) => (
          <li key={m.id} className="flex items-center gap-2 rounded-lg border border-ink-800 p-1.5">
            {m.thumb ? <img src={m.thumb} alt="" className="h-10 w-16 rounded object-cover" /> : <div className="h-10 w-16 rounded bg-ink-800" />}
            <div className="min-w-0 flex-1 text-sm">
              <div className="truncate text-ink-100">{m.name}</div>
              <div className="text-xs text-ink-400">{m.kind} map · from day {m.from_day}</div>
            </div>
            <Button variant="primary" disabled={!!busy} onClick={() => run("show", async () => {
              const r = await api.toTable(detail.id, { branch, day, map_doc_id: m.id });
              return `On the table: ${r.map?.name ?? detail.name}. Scene sent (${plural(r.scene.people, "person", "people")}, ${plural(r.scene.rumors, "rumor")}).`;
            })}>{busy === "show" ? <Spinner /> : "▶"} Show</Button>
            <Button variant="ghost" disabled={!!busy} title="Unlink from this day on" onClick={() => run("unlink", async () => {
              await api.unlinkMap(m.id, branch, day); onChanged(); return "Unlinked.";
            })}>✕</Button>
          </li>
        ))}
      </ul>
      {!maps.length && (
        <Button className="mt-1" disabled={!!busy} onClick={() => run("scene", async () => {
          const r = await api.toTable(detail.id, { branch, day });
          return `Scene sent to the VTT's DM screen (${plural(r.scene.people, "person", "people")}, ${plural(r.scene.rumors, "rumor")}). Link a map to show it on the table too.`;
        })}>{busy === "scene" ? <Spinner /> : "↗"} Send scene to the VTT</Button>
      )}
      <div className="mt-2 flex gap-1.5">
        <select className={cx(inputCls, "py-1 text-xs")} value={pick}
          onFocus={() => !library && api.vttMaps().then(setLibrary).catch((e) => setErr(e.message))}
          onChange={(e) => setPick(e.target.value)}>
          <option value="">{library ? "Link a VTT map…" : "Link a VTT map… (loading library)"}</option>
          {library?.map((m) => <option key={m.id} value={m.id}>{m.title}{m.source ? ` (${m.source})` : ""}</option>)}
        </select>
        <Button disabled={!pick || !!busy} onClick={() => run("link", async () => {
          await api.linkMap(detail.id, { vtt_map_id: pick, branch, day });
          setPick(""); onChanged();
          return `Linked from day ${day ?? "now"}.`;
        })}>{busy === "link" ? <Spinner /> : "Link"}</Button>
      </div>
      <PaintPlace detail={detail} branch={branch} day={day} onChanged={onChanged} busy={!!busy}
        onTokens={() => run("tokens", async () => {
          const r = await api.paintTokens(detail.id, { branch, day });
          if (!r.queued.length) return r.already.length ? "Everyone here already has a portrait." : "Nobody is here to paint.";
          return `Painting ${plural(r.queued.length, "portrait")} on the VTT${r.already.length ? ` (${r.already.length} already done)` : ""}. They show in the World panel next time you send the scene.`;
        })} tokensBusy={busy === "tokens"} />
      {note && <p className="mt-1.5 text-xs text-emerald-300">{note}</p>}
      {err && <p className="mt-1.5 text-xs text-rose-300">{err}</p>}
    </div>
  );
}

/** Paint this place on the VTT as it stands on this branch and day, then link the map from that day. */
function PaintPlace({ detail, branch, day, onChanged, busy, onTokens, tokensBusy }: {
  detail: PlaceDetail; branch: string; day: number | null; onChanged: () => void;
  busy: boolean; onTokens: () => void; tokensBusy: boolean;
}) {
  const [brief, setBrief] = useState<MapBrief | null>(null);
  const [text, setText] = useState("");
  const [job, setJob] = useState<PaintStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setBrief(null); setJob(null); setErr(null); }, [detail.id, branch, day]);

  const open = async () => {
    setErr(null);
    try {
      const b = await api.mapBrief(detail.id, branch, day);
      setBrief(b); setText(b.description);
    } catch (e) { setErr((e as Error).message); }
  };

  const paint = async () => {
    if (!brief) return;
    setErr(null);
    try {
      const r = await api.paintMap(detail.id, { branch, day: brief.day, description: text !== brief.description ? text : undefined });
      setJob({ job: r.job, status: "queued", progress: 0, message: "Queued", error: null, linked: null });
      setBrief(null);
      for (;;) {
        await new Promise((res) => setTimeout(res, 2000));
        if (!alive.current) return;
        const s = await api.collectPaint(r.job);
        if (!alive.current) return;
        setJob(s);
        if (s.status === "error") { setErr(s.error ?? "The VTT couldn't paint it"); return; }
        if (s.linked) { onChanged(); return; }
      }
    } catch (e) { setErr((e as Error).message); }
  };

  const running = job && !job.linked && job.status !== "error";
  return (
    <div className="mt-2 border-t border-ink-800 pt-2">
      {!brief && (
        <div className="flex flex-wrap gap-1.5">
          <Button disabled={busy || !!running} onClick={open} title="Paint a battle map of this place as it is now, from world state">
            ✦ Paint it as it is{day !== null ? ` on day ${day}` : ""}
          </Button>
          <Button variant="ghost" disabled={busy} onClick={onTokens} title="Portrait tokens for the people here">
            {tokensBusy ? <Spinner /> : "◉"} Portraits
          </Button>
        </div>
      )}
      {brief && (
        <div className="space-y-1.5">
          <div className="text-xs text-ink-400">
            The VTT paints from this brief{brief.events.length ? `, after ${brief.events.join("; ")}` : ""}. Edit it if you like.
          </div>
          <textarea className={cx(inputCls, "h-28 resize-y text-xs leading-snug")} maxLength={600} value={text}
            onChange={(e) => setText(e.target.value)} />
          <div className="flex gap-1.5">
            <Button variant="primary" disabled={text.trim().length < 3} onClick={paint}>Paint “{brief.title}”</Button>
            <Button variant="ghost" onClick={() => setBrief(null)}>Cancel</Button>
          </div>
        </div>
      )}
      {job && (
        <p className={cx("mt-1.5 text-xs", job.linked ? "text-emerald-300" : "text-ink-300")}>
          {job.linked
            ? `Painted and linked from day ${job.linked.day}. Press Show to put it on the table.`
            : job.status === "error" ? null
            : <><Spinner /> Painting on the VTT… {job.progress != null ? `${Math.round(job.progress * 100)}%` : ""} {job.message ?? ""}</>}
        </p>
      )}
      {err && <p className="mt-1.5 text-xs text-rose-300">{err}</p>}
    </div>
  );
}

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}
