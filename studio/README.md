# SpotForge Studio

From this directory run `npm ci`, `npm test`, and `npm run build`. `npm run dev` serves localhost:4832 and proxies `/api` to the local Python API on 4831. Install `../renderer` dependencies too: the composition source is shared. Python serves `dist` for production.

The studio provides projects, scene controls, explicit billable generation confirmation, uploaded frame/media selection, take selection, versioned brand application, audio/caption editing, manual spot approvals, job progress and exports. It uses the contract in `docs/spotforge/contracts.md`, with same-origin API calls. The brand-profile editor is the independently owned `src/BrandPanel.jsx`; the build discovers it when the brand module is integrated.

Generated scenes must be saved before submitting; confirmation includes the current revision. Unsupported model modes are disabled. An end-only request is not silently translated: the user is guided to add a start image and choose start+end. Existing provider jobs are reconciled explicitly, never blindly resubmitted.

The preview uses the same `VideoComposition` and manifest as the render worker. Use “Aktualisieren” / “Take im Projekt laden” to read completed generation results. Job statuses poll automatically. Scene ordering follows project creation order in this first version. Captions use explicit millisecond timestamps; karaoke also needs word timestamps. Production export does not require the Player to have rendered first; validation and approval remain backend responsibilities.
