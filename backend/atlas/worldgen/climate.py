"""Climate and biomes from where a region sits and the shape of its land (phase 3, #41).

* **Temperature:** mean annual sea-level temperature falls with latitude (30 °C at the equator, about 10 °C at 45°),
  then 6.5 °C per km of altitude (heights 20-100 map to 0-5 km on a squared curve: most land is low).
* **Rain:** prevailing winds by latitude band blow across the map: trade winds (0-30°) from the east, westerlies
  (30-60°) from the west, polar easterlies beyond. Air carries moisture picked up over the sea; over land it rains out
  steadily, and heavily where it is forced up slopes, so the far side of a range lies in a **rain shadow**. A
  latitude factor adds the wet equator, the dry subtropics (~25°) and the wet mid-latitudes.
* **Biomes:** a Whittaker-style table from temperature and rain to Azgaar's 13 biome ids (the atlas colours,
  ``BIOME_COST`` and the bestiary all key on them); low, wet, poorly drained ground becomes wetland.
"""
from __future__ import annotations

import numpy as np

SEA_LEVEL = 20


def latitude_of(centers: list[tuple[float, float]], height: float, lat_n: float, lat_t: float) -> np.ndarray:
    return np.array([lat_n - y / height * lat_t for _, y in centers])


def altitude_km(h: np.ndarray) -> np.ndarray:
    """Height (Azgaar 20-100) to km: most land is low and only peaks are high, so the curve is squared
    (45 -> 0.5 km, 67 -> 1.7 km, 100 -> 5 km)."""
    return np.clip((h - SEA_LEVEL) / 80, 0, 1) ** 2 * 5.0


def temperature(lat: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Mean annual temperature (°C) per cell."""
    return 30 - 0.45 * np.abs(lat) - 6.5 * altitude_km(h)


def wind_east(lat: float) -> bool:
    """Does the prevailing wind at this latitude blow toward the east (westerlies) rather than the west?"""
    return 30 <= abs(lat) < 60


def rain_by_latitude(lat: np.ndarray) -> np.ndarray:
    """Wet equator, dry subtropical highs, wet mid-latitude storm tracks, dry poles."""
    a = np.abs(lat)
    return (0.55 + 0.75 * np.exp(-(a / 10) ** 2) - 0.45 * np.exp(-((a - 25) / 8) ** 2)
            + 0.30 * np.exp(-((a - 50) / 12) ** 2) - 0.35 * np.clip((a - 65) / 20, 0, 1))


def precipitation(centers: list[tuple[float, float]], nbrs: list[list[int]], h: list[int], lat: np.ndarray) -> np.ndarray:
    """Relative yearly rain per cell (land mean around 1), from moisture carried downwind over the cells."""
    n = len(centers)
    hh = np.asarray(h, dtype=float)
    xs = np.array([x for x, _ in centers])
    east = np.array([wind_east(la) for la in lat])
    # visit cells in the order the wind reaches them: west to east under westerlies, east to west otherwise
    key = np.where(east, xs, -xs)
    order = np.argsort(key, kind="stable")
    moisture = np.zeros(n)
    rain = np.zeros(n)
    alt = altitude_km(hh)
    for i in order:
        up = [j for j in nbrs[i] if key[j] < key[i]]
        m = float(np.mean(moisture[up])) if up else 1.0  # air arriving from beyond the map edge is sea air
        if hh[i] < SEA_LEVEL:
            moisture[i] = min(1.0, m + 0.15)  # evaporation over open water
            continue
        lift = max(0.0, alt[i] - (float(np.mean(alt[up])) if up else 0.0))  # rising over a slope
        r = m * (0.06 + 2.2 * lift)
        r = min(r, m * 0.9)
        rain[i] = r
        moisture[i] = m - r * 0.85 + 0.02  # a little recycled by plants
    land = hh >= SEA_LEVEL
    rain = rain * rain_by_latitude(lat)
    if land.any():
        rain[land] = rain[land] / max(float(rain[land].mean()), 1e-9)
    return rain


def biome_for(t: float, p: float, h: int, wet: float) -> int:
    """Azgaar biome id for temperature ``t`` (°C), relative rain ``p``, height ``h`` and drainage ``wet`` (0-1)."""
    if h < SEA_LEVEL:
        return 0
    if t < -4:
        return 11  # glacier
    if t < 1:
        return 10  # tundra
    if h < 26 and wet > 0.6 and p > 0.9 and t > 2:
        return 12  # wetland: low, wet, slow to drain
    if t < 6:
        return 2 if p < 0.35 else 9  # cold desert, taiga
    if t < 18:  # temperate
        if p < 0.35:
            return 2 if t < 12 else 1   # cold desert / hot desert
        if p < 0.7:
            return 4                    # grassland
        if p < 1.45:
            return 6                    # temperate deciduous forest
        return 8                        # temperate rainforest
    if p < 0.3:
        return 1   # hot desert
    if p < 0.7:
        return 3   # savanna
    if p < 1.4:
        return 5   # tropical seasonal forest
    return 7       # tropical rainforest


def biomes(temp: np.ndarray, rain: np.ndarray, h: list[int], flux: list[float] | None = None) -> list[int]:
    """Biome per cell. ``flux`` (from hydrology) marks poorly drained lowland where water gathers."""
    n = len(h)
    if flux:
        f = np.asarray(flux, dtype=float)
        land = np.asarray(h) >= SEA_LEVEL
        ref = float(np.quantile(f[land], 0.9)) if land.any() else 1.0
        wet = np.clip(f / max(ref, 1e-9), 0, 1)
    else:
        wet = np.zeros(n)
    return [biome_for(float(temp[i]), float(rain[i]), h[i], float(wet[i])) for i in range(n)]


def summary(biome: list[int], h: list[int]) -> dict[int, float]:
    land = [b for b, x in zip(biome, h) if x >= SEA_LEVEL]
    return {b: round(land.count(b) / len(land), 3) for b in sorted(set(land))} if land else {}

