# Spatial explorer

See the [project README](../README.md) for setup, tests, data handling, and attribution.

`app.js` coordinates the scene and playback. Geometry, internal paths, schematic façades, apartment layout, interior controls, and charts live in separate modules. `model-data.json` contains public OSM geometry only; recordings come from `/api/recordings`.

Run `npm test` here for geometry tests. `npm run export` creates an ignored OBJ of the baseline community model. The portal's export button uses the currently selected model settings.
