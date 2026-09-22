# SpotForge contracts v1

Authoritative Pydantic models: `spotforge/models.py`; generated schemas: `contracts/*.json`. Store interface: `.read(collection,id)`, `.write(collection,id,dict-or-model)`, `.list(collection)`, `.asset_path(id)`, `.add_asset(bytes,name,kind,mime)`; `.lock` is a reentrant transaction lock, `.root` is the data directory. Collections: projects, assets, brands, brand_versions (key id_version), jobs. Never write returned dicts without holding store.lock for the read/modify/write cycle.

## HTTP contract
All errors are JSON `{detail: string}` (validation may return FastAPI detail array). Success uses plain JSON, no wrappers. UI base same-origin `/api`.
- GET /api/health -> {ok, ...}
- GET /api/projects -> Project[]; POST /api/projects {title,recipe?,format?,brand_id?,brand_version?} -> Project
- GET /api/projects/{id} -> Project; PATCH same -> Project (title,brief,script,format,audio,captions only; optional expected_revision)
- POST /api/projects/{id}/scenes -> Scene input -> Project
- POST /api/projects/{id}/scenes/reorder {scene_ids,expected_revision} -> Project; exact permutation, dependencies stay ordered, existing time-coded captions must first be removed/re-timed
- PATCH /api/projects/{id}/scenes/{sid} -> Scene patch -> Project; DELETE same -> Project
- POST /api/projects/{id}/scenes/{sid}/select {take_id} -> Project
- POST /api/projects/{id}/gates/{gate} {approve: bool} -> Project (only explicit user UI action)
- POST /api/assets multipart file -> Asset; GET /api/assets -> Asset[]; GET /api/assets/{id}/file -> bytes
- GET /api/brands -> Brand[] (latest); POST /api/brands Brand input -> Brand
- GET /api/brands/{id}?version=N -> Brand; POST /api/brands/{id}/versions Brand input -> new Brand version
- POST /api/projects/{id}/brand {brand_id,version?} -> Project (explicit applies snapshot; invalidate output/freigaben)
- GET /api/models -> capability objects (id,label,modes)
- GET /api/workflow/capabilities -> installed local speech voices and explicit stock credential availability
- POST /api/projects/{id}/speech {confirmed,expected_revision,voice,rate} -> durable speech Job; saved script becomes local PCM audio and sentence captions, applied only if project revision is unchanged
- POST /api/stock/search {provider,query,orientation,confirmed} -> candidates with server-owned selection IDs
- POST /api/projects/{id}/stock/import {selection_id,expected_revision,confirmed,scene_id?} -> stock_import Job; result retains local asset/source metadata even if project changed
- POST /api/projects/{id}/scenes/{sid}/generate {confirmed:bool,expected_revision:int} -> Job
- GET /api/jobs -> Job[]; GET /api/jobs/{id} -> Job
- POST /api/jobs/{id}/resume {confirmed:bool,provider_request_id?:string} -> Job; polls an existing request without resubmission. Explicit ID assignment is only allowed for unknown jobs without an ID.
- POST /api/jobs/{id}/cancel -> {id,state}; local renders only, not provider jobs
- POST /api/projects/{id}/render -> Job; GET /api/projects/{id}/manifest -> RenderManifest for preview
- GET /api/outputs/{job_id}/preview -> MP4 before final human approval
- GET /api/outputs/{job_id} -> MP4; spot projects require final approval of this current export
- POST /api/imports/preview {source_dir,project_file?,brand_file?} -> token, summary, media, warnings, errors, can_import; no writes
- POST /api/imports/commit {source_dir,project_file?,brand_file?,token,confirmed} -> new Project; source fingerprint must still match
- GET /api/imports/{project_id}/report -> preserved media/frame-take/export mappings and visible migration warnings

Agent A creates `spotforge/generation.py` + optional `generation_routes.py` exporting `create_router(store)` implementing models/generate/resume. Provider credentials ONLY explicit environment FAL_KEY, no credential discovery or paid tests. No auto model fallback. It owns no main API/store/models changes; propose necessary additions to coordinator. Jobs use common collection. Final brand/gate/read-modify-write validation under lock; network work outside lock.
Agent B creates `spotforge/brands.py` exporting `create_router(store)` implementing brands + project brand routes, validation/snapshots; optional `studio/src/BrandPanel.jsx` default component props `{brands,assets,onRefresh,onError}` using same-origin API. Coordinate directly with C. `colors` keys primary/background/text. Fonts and logos assets must exist and match kind. Profile versions immutable.
Agent C creates renderer + studio. Node `renderer/render.mjs <manifest.json> <output.mp4>` renders verified manifest via bundled composition, stdout JSON lines `{progress:0..1}` and final `{output:...}`. Manifest assets entries include `url`, `kind`, `name`, `sha256`; media browser URLs provided by Python. Export the composition to reuse in studio Player. Keep dependencies pinned, scripts build/test, npm lockfiles. Full composition includes video/image scenes, overlay text, logo, font, captions, narration/music. No paid generation. Renderer must error on missing required assets. Parent Python controls lifecycle and output path.

MVP UI: project list/create, title/format/brand, scene list/add/edit, frame upload/dropdowns, modes and unsupported/end-assisted explanation, model selector, local video take/import, audio asset selection and captions JSON editing, gates for spot, job status, render/download. Structured brand panel. Use existing app visual components/styles where practical; don't preserve incompatible Node-only APIs.

The coordinator implements project/scene/assets/gates/render API, render worker, local fixture/launcher/tests. Root API mounts A/B routers. Projects are read/written with models validation. No auto-approved cost/gate decisions.
