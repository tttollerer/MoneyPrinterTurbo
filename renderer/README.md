# Local renderer

Run `npm ci`, `npm test`, and `npm run build` in this directory.

`node render.mjs /absolute/path/manifest.json /absolute/path/output.mp4`

The parent Python process owns job lifecycle and output paths. The worker validates required asset references, probes their HTTP endpoints and emits JSON progress on stdout. Browser resources, including uploaded brand fonts, must be reachable by absolute HTTP URLs. Media endpoints support HEAD and Range GET. Errors go to stderr and exit nonzero; there is no silent substitute renderer or brand-font fallback.

`src/Composition.jsx` is shared with Studio's Remotion Player. The manifest sets format and duration, consecutive image/video scenes, caption timing, pinned brand styling and audio. Uploaded fonts are loaded before frames render. Karaoke requires explicit word timings. Clips use cover fit; music loops, narration plays once, original clip audio follows the manifest switch. Keep source clips at least as long as their scene. Render concurrency is one on the local Mac.

The Python API validates the full Pydantic contract; `validate.mjs` adds cross-reference and timeline checks before rendering. The real offline fixture/render verification is driven by the root SpotForge smoke workflow.
