# Azgaar's Fantasy Map Generator, self-hosted

[Azgaar's Fantasy Map Generator](https://github.com/Azgaar/Fantasy-Map-Generator) (MIT, © Max Haniyeu) runs here as
the `fmg` service, so you can make and hand-edit worlds on your own machine or LAN without the online version:
`http://<this-machine>:${FMG_PORT:-8070}`.

To use a map in the engine: in the generator, **Menu → Export → Export to JSON → Full**, then in the dashboard
**✦ New World → World → Map source: Use my Azgaar export** and upload the `.json` (or
`python scripts/import_azgaar.py MyWorld_Full.json`). See [docs/azgaar-field-guide.md](../docs/azgaar-field-guide.md).

- **Pinned version:** `FMG_REF` (default `v1.157.0`, a git tag of the upstream repo). To upgrade, set it in `.env`
  and `docker compose build fmg`.
- **Build:** the upstream repo at that tag, built with `vite build --mode electron` (the desktop renderer's build:
  served from `/`, without Google Analytics or PWA tags). It behaves as the web version.
- **Privacy:** nginx sends upstream's Content-Security-Policy (`default-src 'self'`), so the page loads nothing
  from, and reports nothing to, other sites. Features that need the internet (the online assistant, loading a map
  from a URL) won't work here.
