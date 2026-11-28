import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { GeoJSONSource, Map as MlMap } from "maplibre-gl";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, AtlasState, AtlasView, Branch, LocalBrief, MapBrief, PaintStatus, AtlasLook, PartyKnows, PartyMapStyle, PlaceDetail, VttMap } from "../api";
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
  const loadedSlug = useRef<string | null>(null);
  const openRef = useRef<(id: string) => void>(() => undefined);
  const footprintIds = useRef<string[]>([]);
  const [ready, setReady] = useState(false);
  const [branch, setBranch] = useState("main");
  const [compare, setCompare] = useState(true);
  const [view, setView] = useState<AtlasView>("dm");
  // the atlas's look: data colours, or the drawn parchment map in colour or ink (#57)
  const [look, setLook] = useState<AtlasLook>("data");
  const [zoom, setZoom] = useState(0);
  const [tableNote, setTableNote] = useState<string | null>(null);
  const [tableBusy, setTableBusy] = useState(false);
  const [mapStyle, setMapStyle] = useState<PartyMapStyle>("colour");
  const partyToTable = async () => {
    setTableBusy(true); setTableNote(null); setError(null);
    try {
      const r = await api.partyMapToTable({ branch, day, style: mapStyle });
      setTableNote(`On the table: ${r.title}. It redraws itself when you mark what the party learns.`);
    } catch (e) { setError((e as Error).message); }
    finally { setTableBusy(false); }
  };
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
      const s = await api.atlas(branch, day, compareTo, view);
      setState(s);
      if (day === null) setDay(s.day);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [branch, day, compareTo, view]);

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
        glyphs: `${location.origin}/fonts/{fontstack}/{range}.pbf`,
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
    map.on("zoomend", () => setZoom(map.getZoom()));
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

  // ------------------------------------------------------------------ the look: data or parchment (#57)
  useEffect(() => {
    const map = mapRef.current;
    const slug = state?.atlas?.slug;
    if (!map || !ready || !slug || !map.getLayer("parchment")) return;
    const parchment = look !== "data";
    if (parchment) {
      (map.getSource("parchment") as maplibregl.RasterTileSource).setTiles(
        [`${location.origin}/api/atlas/parchment/${slug}/{z}/{x}/{y}.webp?style=${look === "ink" ? "ink" : "colour"}`]);
    }
    map.setLayoutProperty("parchment", "visibility", parchment ? "visible" : "none");
    for (const id of ["biomes", "rivers", "roads", "sea-lanes"]) {  // drawn into the parchment instead
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", parchment ? "none" : "visible");
    }
    if (map.getLayer("province-fill")) map.setPaintProperty("province-fill", "fill-opacity", parchment ? 0.22 : 0.5);
    if (map.getLayer("province-line")) map.setPaintProperty("province-line", "line-opacity", parchment ? 0.35 : 0.7);
    // lettering: ink on paper, or light on the dark data map
    for (const l of map.getStyle().layers ?? []) {
      if (l.type !== "symbol" || !l.id.startsWith("label-") || l.id === "label-province") continue;
      if (l.id.startsWith("label-water")) {
        map.setPaintProperty(l.id, "text-color", parchment ? (look === "ink" ? "#2e2822" : "#34566e") : "#b9d6ea");
        map.setPaintProperty(l.id, "text-halo-color", parchment ? "#e8e2cc" : "#0b0f16");
        continue;
      }
      map.setPaintProperty(l.id, "text-color", parchment
        ? ["case", ["==", ["get", "start"], true], "#8a4a10", "#281e14"]
        : ["case", ["==", ["get", "start"], true], "#f5c77e", ["get", "campaign"], "#f3d9a8", "#e6eaf0"]);
      map.setPaintProperty(l.id, "text-halo-color", parchment ? "#f1e8cf" : "#0b0f16");
    }
  }, [look, ready, state?.atlas?.slug, view]);

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
    // Parchment: the party map's drawn look as raster tiles (atlas/parchment.py); hidden until chosen
    map.addSource("parchment", {
      type: "raster", tileSize: 256, minzoom: 3, maxzoom: 11,
      tiles: [`${location.origin}/api/atlas/parchment/${a.slug}/{z}/{x}/{y}.webp?style=colour`],
    });
    map.addLayer({ id: "parchment", type: "raster", source: "parchment", minzoom: 3, layout: { visibility: "none" },
      paint: { "raster-fade-duration": 150 } });
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
      filter: ["!=", ["get", "group"], "searoutes"],
      paint: {
        "line-color": ["match", ["get", "group"], "highways", "#7a2d1e", "trails", "#8a6a3f", "#6b4f2a"],
        "line-width": ["match", ["get", "group"], "highways", 1.8, "trails", 0.7, 1],
        "line-dasharray": [3, 2], "line-opacity": 0.8,
      },
    });
    // Sea lanes between ports: sailed, not walked (atlas/travel.py)
    map.addLayer({
      id: "sea-lanes", type: "line", source: src("roads"), ...srcLayer("roads"), minzoom: 3,
      filter: ["==", ["get", "group"], "searoutes"],
      paint: { "line-color": "#cfe6ff", "line-width": 1, "line-dasharray": [1, 2], "line-opacity": 0.75 },
    });
    // Party view: fog over provinces the party doesn't know (0 seen, 1 heard of, 2 unknown)
    map.addLayer({
      id: "party-fog", type: "fill", source: src("provinces"), ...srcLayer("provinces"),
      paint: {
        "fill-color": ["match", ["coalesce", ["feature-state", "fog"], 0], 2, "#0b0f16", "#1c2433"],
        "fill-opacity": ["match", ["coalesce", ["feature-state", "fog"], 0], 2, 0.9, 1, 0.5, 0],
        "fill-opacity-transition": { duration: 500 },
      },
    });
    // live layers
    const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
    map.addSource("places", { type: "geojson", data: empty });
    map.addSource("events", { type: "geojson", data: empty });
    map.addSource("rumors", { type: "geojson", data: empty });
    map.addSource("footprints", { type: "geojson", data: empty });
    map.addSource("route", { type: "geojson", data: empty });
    // Province names: one label per province (a point at its centre, not per-tile polygon labels, which repeat), faint,
    // once zoomed in a little; in the party view only the provinces the party knows are named
    map.addSource("province-labels", { type: "geojson", data: empty });
    map.addLayer({
      id: "label-province", type: "symbol", source: "province-labels", minzoom: 5,
      layout: {
        "text-field": ["upcase", ["get", "name"]], "text-font": ["Noto Sans Italic"], "text-size": 10,
        "text-letter-spacing": 0.15, "text-max-width": 8, "text-padding": 6, "symbol-sort-key": 10,
      },
      paint: {
        "text-color": "#2a2418", "text-halo-color": "#f3ead2", "text-halo-width": 1.4,
        "text-opacity": ["case", ["==", ["get", "known"], "heard"], 0.5, 0.8],
      },
    });
    // Seas, gulfs, bays, straits and lakes (atlas/waters.py): slanted, spaced, sea-blue (DM view)
    map.addSource("waters", { type: "geojson", data: empty });
    fetch(`${location.origin}${api.atlasFile(a.slug, "waters.geojson")}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((fc: GeoJSON.FeatureCollection | null) => {
        if (!fc) return;
        const pts = fc.features.filter((f) => f.properties?.label).map((f) => ({
          type: "Feature" as const, properties: f.properties,
          geometry: { type: "Point" as const, coordinates: f.properties!.label as number[] },
        }));
        (map.getSource("waters") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: pts });
      })
      .catch(() => undefined);
    const waterLabel = (id: string, kinds: string[], minzoom: number) => map.addLayer({
      id, type: "symbol", source: "waters", minzoom, filter: ["in", ["get", "kind"], ["literal", kinds]],
      layout: {
        "text-field": ["case", ["==", ["get", "kind"], "lake"], ["get", "name"], ["upcase", ["get", "name"]]],
        "text-font": ["Noto Sans Italic"],
        "text-size": ["match", ["get", "kind"], "sea", 15, "gulf", 12, 11],
        "text-letter-spacing": ["case", ["==", ["get", "kind"], "lake"], 0.05, 0.3], "text-max-width": 14,
        "symbol-sort-key": 20,
      },
      paint: { "text-color": "#b9d6ea", "text-halo-color": "#0b0f16", "text-halo-width": 1, "text-opacity": 0.9 },
    });
    waterLabel("label-water", ["sea", "gulf"], 3);
    waterLabel("label-water-small", ["bay", "strait", "lake"], 5.5);
    map.addLayer({
      id: "route", type: "line", source: "route",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#f5c77e", "line-width": 2.5, "line-dasharray": [1.5, 1.5], "line-opacity": 0.9 },
    });
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
    // Place names as real map labels (glyphs bundled in /fonts): they collide instead of piling up, the start town and
    // capitals win, smaller places appear as you zoom in, and nothing on the far side of the globe is drawn.
    const placeLabel = (id: string, filter: any, minzoom: number, size: number, font = "Noto Sans Regular") =>
      map.addLayer({
        id, type: "symbol", source: "places", minzoom, filter,
        layout: {
          "text-field": ["get", "name"], "text-font": [font], "text-size": size, "text-anchor": "bottom",
          "text-offset": [0, -0.6], "text-max-width": 9, "text-padding": 3,
          "symbol-sort-key": ["case", ["==", ["get", "start"], true], 0, ["==", ["get", "kind"], "capital"], 1,
            ["==", ["get", "kind"], "city"], 2, ["==", ["get", "kind"], "town"], 3, 4],
        },
        paint: {
          "text-color": ["case", ["==", ["get", "start"], true], "#f5c77e", ["get", "campaign"], "#f3d9a8", "#e6eaf0"],
          "text-halo-color": "#0b0f16", "text-halo-width": 1.4, "text-halo-blur": 0.4,
        },
      });
    map.addLayer({
      id: "label-continent", type: "symbol", source: "places", maxzoom: 4.5, filter: ["==", ["get", "kind"], "continent"],
      layout: {
        "text-field": ["upcase", ["get", "name"]], "text-font": ["Noto Sans Italic"], "text-size": 14,
        "text-letter-spacing": 0.25, "text-max-width": 12,
      },
      paint: { "text-color": "#f3ead2", "text-opacity": 0.85, "text-halo-color": "#0b0f16", "text-halo-width": 1.2 },
    });
    const notStart = ["!=", ["get", "start"], true];  // the start town has its own (capital) label
    placeLabel("label-local", ["all", notStart, ["!", ["in", ["get", "kind"], ["literal", ["continent", "capital", "city", "town"]]]]], 9, 11);
    placeLabel("label-town", ["all", notStart, ["==", ["get", "kind"], "town"]], 6.5, 11);
    placeLabel("label-city", ["all", notStart, ["==", ["get", "kind"], "city"]], 5, 12);
    placeLabel("label-capital", ["any", ["==", ["get", "kind"], "capital"], ["==", ["get", "start"], true]], 0, 12.5,
      "Noto Sans Medium");
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
        { color: p.color, changed: p.changed, fog: p.known === "visited" ? 0 : p.known === "heard" ? 1 : 2 },
      );
    }
    (map.getSource("places") as GeoJSONSource).setData(state.places);
    (map.getSource("province-labels") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: Object.values(state.provinces)
        .filter((p) => p.name && p.lon !== null && p.lat !== null && (state.view !== "party" || p.known))
        .map((p) => ({ type: "Feature", properties: { name: p.name, known: p.known },
          geometry: { type: "Point", coordinates: [p.lon as number, p.lat as number] } })),
    });
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
  }, [state, showSecret]);

  const openDetail = useCallback(
    (id: string) => {
      if (view === "party" && id.startsWith("Province/")) {
        const p = Object.values(stateRef.current?.provinces ?? {}).find((x) => x.id === id);
        if (p && !p.known) return; // fog: nothing to show
      }
      api.place(id, branch, day, view).then(setDetail).catch((e) => setError(e.message));
    },
    [branch, day, view],
  );
  openRef.current = openDetail;

  // The way from the party to the open place, drawn on the globe
  useEffect(() => {
    const src = mapRef.current?.getSource("route") as GeoJSONSource | undefined;
    if (!src) return;
    const path = detail?.travel?.path;
    src.setData({ type: "FeatureCollection", features: path && path.length > 1
      ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: path } }] : [] });
  }, [detail, state]);
  const stateRef = useRef<AtlasState | null>(null);
  stateRef.current = state;

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
            <div className="inline-flex rounded-lg bg-ink-800 p-0.5 ring-1 ring-ink-700" title="Party view: only what the player characters know">
              {(["dm", "party"] as const).map((v) => (
                <button key={v} type="button"
                  className={cx("rounded-md px-2.5 py-1 text-xs font-medium", view === v ? "bg-amber-500 text-ink-950" : "text-ink-300 hover:text-ink-100")}
                  onClick={() => { setView(v); setDetail(null); }}>
                  {v === "dm" ? "DM view" : "Party view"}
                </button>
              ))}
            </div>
<div className="inline-flex rounded-lg bg-ink-800 p-0.5 ring-1 ring-ink-700" title="The globe's look: data colours, or the drawn parchment map, in colour or ink (from regional zoom)">
              {([["data", "Data"], ["colour", "Parchment"], ["ink", "Ink"]] as const).map(([v, label]) => (
                <button key={v} type="button"
                  className={cx("rounded-md px-2.5 py-1 text-xs font-medium", look === v ? "bg-amber-500 text-ink-950" : "text-ink-300 hover:text-ink-100")}
                  onClick={() => setLook(v)}>
                  {label}
                </button>
              ))}
            </div>
            {view === "party" && (
              <>
                <Button variant="primary" disabled={tableBusy || !state?.party?.name} onClick={partyToTable}
                  title="Draw this view as an overland map and show it on the players' table">
                  {tableBusy ? <Spinner /> : "▶"} Party map on the table
                </Button>
    <div className="inline-flex rounded-lg bg-ink-800 p-0.5 ring-1 ring-ink-700" title="The party map's look: colour washes, or paper and ink only like Tolkien's maps">
                  {([["colour", "Colour"], ["ink", "Ink"]] as const).map(([v, label]) => (
                    <button key={v} type="button"
                      className={cx("rounded-md px-2.5 py-1 text-xs font-medium", mapStyle === v ? "bg-amber-500 text-ink-950" : "text-ink-300 hover:text-ink-100")}
                      onClick={() => setMapStyle(v)}>
                      {label}
                    </button>
                  ))}
                </div>
                <a className="text-xs text-ember-300 hover:underline" href={api.partyMapUrl(branch, day, mapStyle)} target="_blank" rel="noreferrer">preview</a>
              </>
            )}
            {view === "dm" && (
              <>
                <label className="flex items-center gap-1.5" title="Highlight what differs from canon">
                  <input type="checkbox" className="accent-amber-500" disabled={branch === "main"} checked={compare && branch !== "main"} onChange={(e) => setCompare(e.target.checked)} />
                  Compare with main
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" className="accent-amber-500" checked={showSecret} onChange={(e) => setShowSecret(e.target.checked)} />
                  Secret events
                </label>
              </>
            )}
            {loading && <Spinner />}
          </div>
        </div>
        <div className="mt-2"><ErrorBox error={error} /></div>
        {tableNote && view === "party" && <p className="mt-1 text-xs text-emerald-300">{tableNote}</p>}
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(300px,1fr)]">
        <div className="relative overflow-hidden rounded-xl border border-ink-700/70">
          <div ref={mapEl} className="h-[70vh] min-h-[480px] w-full" />
          {view === "dm" && zoom >= LOCAL_ZOOM && (
            <LocalPaint mapRef={mapRef} branch={branch} day={day}
              onLinked={(place) => { load(); openDetail(place); }} />
          )}
          {state?.atlas && (
            <div className="pointer-events-none absolute left-3 top-3 rounded-lg bg-ink-950/80 px-3 py-2 text-xs text-ink-200">
              <div className="font-display text-sm text-ink-100">{state.atlas.name}</div>
              <div>Day {state.day} · {state.branch}{compareTo && view === "dm" ? " vs main" : ""}</div>
              {state.view === "party" && (
                <div className="mt-1 border-t border-ink-700 pt-1 text-ember-300">
                  {state.party?.name
                    ? <>What {state.party.name} knows{state.party.where ? <> · at <b>{state.party.where}</b></> : ""}<br />
                        <span className="text-ink-300">{plural(state.party.visited, "place")} visited · {state.party.heard} heard of</span></>
                    : "This world has no party faction yet"}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="space-y-4">
          {detail ? (
            <Card title={detail.name} subtitle={[detail.type, detail.kind, detail.population ? `pop. ${detail.population.toLocaleString()}` : null].filter(Boolean).join(" · ")}
              actions={<Button variant="ghost" onClick={() => setDetail(null)}>✕</Button>}>
              {detail.view === "party" && (
                <p className="mb-1 text-xs text-ember-300">
                  {detail.party?.here ? "The party is here. " : ""}
                  {detail.party?.state === "visited" ? "The party has been here." : "The party has only heard of this place."}
                </p>
              )}
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
              {detail.travel && <TravelInfo t={detail.travel} />}
              {detail.view !== "party" && detail.party && (
                <PartyMark detail={detail} branch={branch} day={day} onChanged={() => { openDetail(detail.id); load(); }} />
              )}
              {detail.type === "Location" && detail.view !== "party" && (
                <TableLink detail={detail} branch={branch} day={day}
                  onChanged={() => { openDetail(detail.id); load(); }} />
              )}
              {!!detail.rumors.length && <Section title="Rumors heard here">{detail.rumors.map((r, i) => <li key={i} className="italic">{r.dialogue} {r.accuracy && <Badge tone={accuracyTone(r.accuracy)}>{r.accuracy}</Badge>}</li>)}</Section>}
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

const LOCAL_ZOOM = 9; // close enough to pick a spot for a battle map

/** Zoomed in close: paint a battle map of the spot under the crosshair, described from the world around it. */
function LocalPaint({ mapRef, branch, day, onLinked }: {
  mapRef: React.MutableRefObject<MlMap | null>; branch: string; day: number | null; onLinked: (place: string) => void;
}) {
  const [brief, setBrief] = useState<LocalBrief | null>(null);
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const look = async () => {
    const c = mapRef.current?.getCenter();
    if (!c) return;
    setErr(null); setStatus(null); setBusy(true);
    try {
      const b = await api.localBrief(+c.lng.toFixed(6), +c.lat.toFixed(6), branch, day);
      setBrief(b); setName(b.name); setText(b.description);
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };

  const paint = async () => {
    if (!brief) return;
    setErr(null); setBusy(true);
    try {
      const r = await api.paintLocal({ lon: brief.look.lon, lat: brief.look.lat, branch, day,
        name: name !== brief.name ? name : undefined, description: text !== brief.description ? text : undefined });
      setBrief(null); setStatus("Queued on the VTT…");
      for (;;) {
        await new Promise((res) => setTimeout(res, 2000));
        if (!alive.current) return;
        const s = await api.collectPaint(r.job);
        if (!alive.current) return;
        if (s.status === "error") { setErr(s.error ?? "The VTT couldn't paint it"); setStatus(null); return; }
        if (s.linked) {
          setStatus(`Painted “${r.name}”. It's on the globe here at true scale; zooming in…`);
          onLinked(r.place);
          mapRef.current?.flyTo({ center: [brief.look.lon, brief.look.lat], zoom: 18.5, speed: 1.2 });
          return;
        }
        setStatus(`Painting on the VTT… ${s.progress != null ? Math.round(s.progress * 100) + "%" : ""}`);
      }
    } catch (e) { setErr((e as Error).message); setStatus(null); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-8 w-8 -translate-x-1/2 -translate-y-1/2">
        <div className="absolute left-1/2 top-0 h-full w-px bg-ember-300/80" />
        <div className="absolute left-0 top-1/2 h-px w-full bg-ember-300/80" />
      </div>
      <div className="absolute bottom-10 left-1/2 w-[min(28rem,90%)] -translate-x-1/2 rounded-lg bg-ink-950/90 p-2.5 text-sm shadow-lg ring-1 ring-ink-700">
        {!brief ? (
          <div className="flex items-center gap-2">
            <Button variant="primary" disabled={busy} onClick={look} title="Describe the spot under the crosshair from the world around it">
              {busy ? <Spinner /> : "✦"} Paint a local map here
            </Button>
            {status && <span className="text-xs text-ink-300">{status}</span>}
          </div>
        ) : (
          <div className="space-y-1.5">
            <input className={cx(inputCls, "py-1 text-sm")} maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
            <textarea className={cx(inputCls, "h-24 resize-y text-xs leading-snug")} maxLength={600} value={text}
              onChange={(e) => setText(e.target.value)} />
            <div className="flex items-center gap-1.5">
              <Button variant="primary" disabled={busy || text.trim().length < 3} onClick={paint}>
                {brief.existing ? "Repaint" : "Paint"} on day {brief.day}
              </Button>
              <Button variant="ghost" onClick={() => setBrief(null)}>Cancel</Button>
              {!brief.look.on_land && <span className="text-xs text-amber-300">That's open water.</span>}
            </div>
          </div>
        )}
        {err && <p className="mt-1 text-xs text-rose-300">{err}</p>}
      </div>
    </>
  );
}

function days(n: number) {
  if (n < 0.5) return "under a day";
  const r = Math.round(n * 2) / 2;
  return `${r} day${r === 1 ? "" : "s"}`;
}

function TravelInfo({ t }: { t: NonNullable<PlaceDetail["travel"]> }) {
  const legs = [t.road_km ? `${t.road_km.toLocaleString()} by road` : "", t.sea_km ? `${t.sea_km.toLocaleString()} by ship` : ""]
    .filter(Boolean);
  return (
    <div className="mt-3">
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-400">Travel from {t.from}</div>
      <div className="text-sm text-ink-200">
        {days(t.days.foot)} on foot · {days(t.days.horse)} on horseback · {days(t.days.army)} for an army
      </div>
      <div className="text-xs text-ink-400">
        {t.km.toLocaleString()} km{legs.length ? ` (${legs.join(", ")})` : " across country"} · news takes {days(t.days.rumor)}
      </div>
    </div>
  );
}

/** DM control: what the party knows of this place, from the day on the slider (bi-temporal, per branch). */
function PartyMark({ detail, branch, day, onChanged }: { detail: PlaceDetail; branch: string; day: number | null; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const p = detail.party!;
  const set = async (state: "visited" | "heard" | "unknown") => {
    setBusy(true); setErr(null);
    try { await api.markParty({ place: detail.id, state, branch, day }); onChanged(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };
  const current: PartyKnows | "unknown" = p.state ?? "unknown";
  const derived = p.state && !p.marked; // known through events, members or a place inside it, not a DM mark
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-ink-400">
        Party knowledge {busy && <Spinner />}
      </div>
      <div className="inline-flex rounded-lg bg-ink-800 p-0.5 ring-1 ring-ink-700">
        {([["visited", "Been there"], ["heard", "Heard of"], ["unknown", "Unknown"]] as const).map(([v, label]) => (
          <button key={v} type="button" disabled={busy}
            className={cx("rounded-md px-2.5 py-1 text-xs font-medium", current === v ? "bg-ember-500/80 text-ink-950" : "text-ink-300 hover:text-ink-100")}
            onClick={() => set(v)}>{label}</button>
        ))}
      </div>
      <p className="mt-1 text-xs text-ink-400">
        {p.here ? "The party is here now. " : ""}
        {derived ? "Known from what the party did or learned; marking it records it from this day." : `Marks apply from day ${day ?? "now"} on this branch.`}
      </p>
      {err && <p className="mt-1 text-xs text-rose-300">{err}</p>}
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
