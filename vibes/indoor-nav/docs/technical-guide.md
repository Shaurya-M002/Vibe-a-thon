# Navsense · indoor navigation explorer

A local-first portal for exploring the last stretch of a delivery: community buildings, an apartment layout, recorded routes, and sensor patterns. It combines an OpenStreetMap-based 2D/3D model with chronological SQLite playback and seven sensor charts.

## Run

Requires Python 3.11+, Node.js 20+, and npm. Run all commands from `vibes/indoor-nav`.

```sh
uv sync --frozen
npm ci --prefix apartment
uv run --frozen python demo.py
uv run --frozen python portal_server.py
```

Open http://127.0.0.1:8765/apartment/. The demo is explicitly synthetic, including its route, sensor values, and 3 m rise. `demo.py` refuses to overwrite an existing database. Map tiles require internet; no map API key is needed.

To use an existing consolidated playback database, extract your private ZIP outside the repository and run:

```sh
uv run --frozen python portal_server.py --database /path/to/recordings.sqlite --port 8765
```

The source Navsense export and the consolidated playback database have different schemas. Supply the consolidated `recordings.sqlite` to the server. It contains the raw source rows plus indexed playback tables. The server reads it with SQLite `mode=ro` and exposes only playback summaries, not raw database downloads.

## What is included

- Community model with OSM building outlines and internal paths; OSM street and Esri satellite basemaps with attribution.
- 2D/3D views, fit controls, fullscreen, building-height controls, and relative pressure height enabled by default.
- A schematic three-bedroom interior with an en-suite opening into the master bedroom and utility between kitchen and common bath.
- Click-to-seek charts for pressure-derived height, steps, movement intensity, total acceleration, turning rate, magnetic field, compass heading, light, pressure, GPS quality, and radio counts where available.
- Offline reconstruction and step-path experiments, kept separate from the portal's recorded GPS display.

## Project structure

| Path | Responsibility |
| --- | --- |
| `apartment/` | Three.js model, controls, charts, and public OSM geometry |
| `recording_store.py` | Read-only SQLite playback queries and shared schema |
| `portal_server.py` | Localhost HTTP server and asset boundary |
| `demo.py` | Reproducible synthetic playback fixture |
| `consolidate.py` | Explicit rebuild of the original 11-recording collection |
| `dashboard/analyze_new.py` | Vertical-event and radio-similarity heuristics |
| `reconstruct.py`, `refine.py`, `joint.py` | Offline inertial reconstruction experiments |
| `gps_heading.py`, `path_variations.py` | GPS diagnostics and alternative step paths |

Personal sensor exports, radio identifiers, videos, reference screenshots, generated models, dependencies, and databases are excluded from Git. The public geometry file contains no recorded sessions.

## Tests

```sh
uv run --frozen python -m unittest discover -v
npm test --prefix apartment
```

Tests use synthetic data and public map geometry. They cover sensor math, event classification, SQLite serialization, overwrite protection, HTTP errors, private-file boundaries, and interior/path geometry. No personal recording is needed.

## Rebuild the original collection

This is an archive-specific importer, not a generic import wizard. It retains original sessions 2 and 3, then the nine sessions from the final export, and renumbers them 1–11. It requires the original private analysis artifacts (`model-data.json` with original sessions, `new_data.json`, and `latest_data.json`) together in one directory. Do not use the public geometry-only `apartment/model-data.json` for this command.

```sh
uv run --frozen python consolidate.py \
  --original /path/to/original/navsense.sqlite \
  --latest /path/to/final/navsense.sqlite \
  --analysis-dir /path/to/private/analysis \
  --output /path/to/recordings.sqlite
```

Source exports are opened read-only. The output is built separately, checked for SQLite integrity and foreign-key consistency, then atomically replaced. Sensor charts use one-second medians; compass headings use a circular mean. Raw samples remain in the consolidated database. These summaries can hide brief peaks.

## Accuracy and attribution

The two main building outlines and OSM level tags come from ways [350866746](https://www.openstreetmap.org/way/350866746) and [146873096](https://www.openstreetmap.org/way/146873096), retrieved 12 September 2026. Geometry: © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). Satellite imagery retains its provider attribution.

Floor-to-floor heights, façades, and interior dimensions are illustrative. The hand-sketched interior has no verified tower/floor assignment or shared corridor geometry. GPS can drift indoors. Pressure indicates relative height change, not an identified floor; magnetic and radio patterns do not establish a location. Lift-like events are heuristic candidates, not confirmed lift rides. This is an exploratory prototype, not a validated turn-by-turn navigation service.
