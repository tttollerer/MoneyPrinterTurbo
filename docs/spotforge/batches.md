# Confirmed sequential generation batches

Mount `spotforge.batches.create_router(store)` under `/api`, alongside the generation router. Both use `generation.get_service(store)`: one queue owner interlocks batch and individual generation. Router lifespan persists a pause on shutdown. This component generates existing scenes; it does not approve gates, invent scenes or automatically render/export videos.

## HTTP contract

- `POST /batches/preview` with `{project_ids: [...]}` persists a preview; 1–20 unique projects and at most 200 scenes. No provider request or generation job is created. The response is `201`.
- `POST /batches/{id}/start` with `{confirmed: true, input_digest: "..."}` verifies the exact preview before starting. JSON booleans are strict; truthy strings are rejected.
- `GET /batches` and `GET /batches/{id}` return status.
- `POST /batches/{id}/pause` stops future submissions; a paid request already submitted can finish.
- `POST /batches/{id}/cancel` cancels unstarted items; it never sends provider cancellation or deletes a paid result.
- `POST /batches/{id}/resume` needs the same explicit confirmation and digest. Known interrupted requests are resumed by ID, never submitted again. Failed/unknown paid requests need manual resolution or a new reviewed preview. A provably unsubmitted job may be restarted only after this explicit resume.

Responses include `id`, `input_digest`, `project_ids`, original `project_revisions`, `state`, `items`, numeric `blocked` and `skipped` counts, `cost`, `error`, and timestamps. Each item has `id`, `project_id`, `project_title`, `scene_id`, `scene_title`, original `revision`, `model`, `mode`, `duration_s`, `action`, `status`, `reason`, and `job_id`. Internal project snapshots are not returned by the HTTP routes.

`cost` is `{amount: null, currency: null, status: "unknown"}`. The UI must show that the total price is unknown before confirmation. This implementation makes no estimated-price claim.

Batch states: `preview`, `running`, `pausing`, `paused`, `cancelling`, `cancelled`, `complete`. Item actions: `generate`, `skip`, `blocked`. Item statuses: `pending`, `skipped`, `blocked`, `running`, `complete`, `needs_review`, `failed`, `interrupted`, `unknown`, `cancelled`.

Conflicts, missing confirmation, changed inputs and unresolved jobs return `409`; bad request shapes return `422`; missing records return `404`. A preview can successfully return blocked items; starting it is refused until a new unblocked preview is created.

## Input and recovery guarantees

The persisted `inputs` object and its digest remain immutable. Projects, current selected media, reference frames and brand files are pinned and validated. Completed non-stale selected takes and ready local scenes are skipped. A dependent scene whose predecessor still needs generation is blocked, including a previously finished dependent take that the predecessor would invalidate. No implicit chain replanning occurs.

Mutable `expected_projects` and `expected_assets` track only the exact allowed output of each completed batch job: appended take, permitted automatic take selection, descendant stale marks, render invalidation, normal clip/final gate reset, and one revision increment. Every other project edit pauses the batch before another paid submission. Changes during an in-flight provider call preserve its result but require a new preview. A stale scene with an existing selected take requires explicit human selection of the new take; the batch pauses for that review.

Jobs are persisted before launch, with their batch ID. A restart pauses the batch and reconstructs missing job links. `GenerationService.recover()` marks known queued/unsubmitted jobs with `result.not_submitted=true`, submitting jobs without a provider ID as `unknown`, and known IDs as `interrupted`. Neither service starts paid work on recovery. Resume can acknowledge a job that finished just before the batch process stopped without creating another job.

The shared in-process `store.batch_owner` and `store.batch_stop_requested` fields supplement durable records. Generation rechecks both immediately before submission. Thus a pause/cancel racing an already queued but unsubmitted job prevents the paid call. A service restart creates fresh in-process state and requires explicit batch resume. The application must continue to use its single local process/single state writer; separate processes sharing the same data directory are not supported.

## Verification

`python -m pytest -q test/services/test_spotforge_batches.py` uses synthetic projects, a fake generator and the real generation service with a fake provider. It verifies strict confirmation, immutable inputs, own versus external revision changes, paid-job failure/resume, interlocks, cancellation before submission, retained in-flight results, recovery gaps and shutdown. No paid calls or credentials are used.
