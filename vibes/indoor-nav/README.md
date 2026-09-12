# Navsense

Explore indoor-navigation recordings with a 2D/3D community map, an apartment floor plan, and sensor playback.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 20+, then run from `vibes/indoor-nav`:

```sh
uv sync --frozen
npm ci --prefix apartment
uv run --frozen python demo.py
uv run --frozen python portal_server.py
```

Open **http://127.0.0.1:8765/apartment/**. The demo uses synthetic data and won’t overwrite an existing database.

To open your recordings:

```sh
uv run --frozen python portal_server.py --database /path/to/recordings.sqlite
```

Use the consolidated playback database, not a raw sensor export. SQLite files and source videos stay outside Git.

## Explore

- Switch between 2D and 3D, fit the route, or open the map fullscreen.
- Inspect the three-bedroom apartment in plan or cutaway view.
- Click sensor charts to move through a recording.

Building heights and the apartment layout are approximate. GPS and pressure don’t identify a verified floor or door. Map geometry: © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).

[Technical guide](docs/technical-guide.md)
