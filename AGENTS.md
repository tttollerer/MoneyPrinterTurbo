# SpotForge fork

MoneyPrinterTurbo upstream remains independently usable. Our local Mac product lives in `spotforge/` (Python), `studio/` (React), and `renderer/` (Node/Remotion). Shared interfaces: `spotforge/models.py`, `contracts/`, `docs/spotforge/contracts.md`.

## Ownership and workflow
One writing agent per worktree/branch/issue. Fetch origin before branching from origin/main. For this initial feature, component branches merge the published `codex/feat/1-spotforge-integration` foundation and open stacked PRs against that branch. The integration PR targets main. No main merge/deployment without user authorization. Never copy private brands, existing project media, secrets or credentials into Git.

## Product rules
- Local files are authoritative; atomic JSON and a single state writer. Runtime data via SPOTFORGE_DATA_DIR, finished outputs via SPOTFORGE_OUTPUT_DIR (default Ausgaben inside the data directory).
- Paid operations require explicit confirmation; spot recipe keeps concept/script/storyboard/clips/final gates, enforced by backend. Do not auto-approve gates.
- Frames condition the provider. Validate capability before submission; end-only needs native support or explicit start-image assistance. No silent fallback dropping frames, typography or branding.
- Preserve takes and pin input assets/brand versions. Changed predecessors mark dependent scenes stale. Rendering never initiates paid generation.
- File paths contain spaces; use argument arrays and pathlib/fileURLToPath. Never shell-interpolate input.
- No secrets in logs, API responses or fixtures. Our default local mode must make no paid calls.

## Verification
Upstream: `uv sync --frozen --python 3.11`; `uv run --no-sync ruff check app cli.py main.py webui test docs/skill spotforge`; `uv run --no-sync python -X utf8 -m pytest -q test`; coverage commands are in `.github/workflows/ci.yml`.
New tests use `test/services/test_spotforge_*.py`. Studio and renderer must provide real package scripts and committed lockfiles. Run their build/tests and a real local render before claiming the end-to-end path works. Use test-generated media, not private project media. Do not write low-value tests that merely duplicate implementation.

## Team ownership
Coordinator: shared contracts/models/store, main API, integration, launcher, smoke example and documentation.
Agent A: `spotforge/generation*.py`, generation tests.
Agent B: `spotforge/brands*.py`, brand tests and `studio/src/BrandPanel.jsx` (coordinate props with C).
Agent C: `renderer/`, `studio/` excluding BrandPanel, renderer/UI tests.
