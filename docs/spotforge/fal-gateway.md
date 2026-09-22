# Local fal gateway

The current curated model remains Kling 2.5 Turbo Pro via fal, with start-image and start+end-image conditioning. Configuring a credential never calls fal and does not verify billing, model access or balance. Generation still requires explicit confirmation and saved-project revision checks. Cost remains reported as unknown.

## Credential lifecycle

Mount `spotforge.credentials_routes.create_router(store)` under `/api`. The optional store argument is unused for secrets.

- `GET /api/providers/fal` returns `{configured, source, persistence_available}` and an optional authored `error`. `source` is `environment`, `session`, `keychain`, or null. Responses never contain a key or a partial-key suffix.
- `PUT /api/providers/fal` accepts `{key, persistence: "keychain" | "session"}` and returns only status. Malformed bodies are handled locally so validation errors never echo the supplied key.
- `DELETE /api/providers/fal` removes the dedicated saved entry and the session value; it does not change an externally supplied `FAL_KEY`.

Resolution is **FAL_KEY → session value → dedicated Keychain entry**. A session override leaves the older saved Keychain value intact; after process restart that saved value becomes active again. Saving to Keychain clears the session override only after the write succeeds. An environment override remains visibly active even after saving or deleting a local key.

Mac persistence calls Security.framework (`SecItemCopyMatching`, `SecItemAdd`, `SecItemUpdate`, `SecItemDelete`) through ctypes. Service `com.spotforge.fal`, account `api-key`. No secret command arguments, shell subprocesses, .env/config discovery, project JSON, browser storage, or plaintext fallback. Other platforms provide session mode. Keychain access is configured to fail rather than unexpectedly display authentication UI; unlock/permit access through macOS or choose session mode if it is unavailable. Library availability does not imply an unlocked Keychain.

## Shared generation and campaigns

`generation.get_service(store)` returns the shared per-store `GenerationService` and performs recovery once. Individual routes and batch runners must use this same instance.

`service.preflight(project_id, scene_id, expected_revision=None, check_active=False, batch_id=None)` validates credentials, gates, model, reference files/aspect, duration and predecessor references without creating a job or making provider calls. Returns safe metadata: project revision, model, mode, duration, configured and unknown cost.

`service.start(project_id, scene_id, confirmed, expected_revision, *, batch_id=None)` and `resume(..., *, batch_id=None)` respect `store.batch_owner`. A batch tags each job's input snapshot with its ID. Before submitting, the worker checks that ownership still matches and `store.batch_stop_requested` is false. A paused/detached job fails with `result.not_submitted=true`, allowing explicit later resumption without ambiguous billing. Recovery also marks queued jobs without a provider ID as `not_submitted`; an interrupted submission without an ID stays `unknown` and must never be blindly retried. A credential removed or replaced during frame preparation prevents that pending submission. Already accepted provider jobs continue through normal polling and durable recovery; credential deletion cannot revoke a request already accepted by fal.

## Tests

Credential and gateway tests use in-memory fake backends and providers. Native acceptance is explicit: `SPOTFORGE_TEST_KEYCHAIN=1 .venv/bin/python -m pytest -q test/services/test_spotforge_credentials.py::test_native_keychain_lifecycle_opt_in`. It uses a randomly named disposable Keychain service with synthetic values and removes only that entry. No paid calls.

References: [Apple SecItemCopyMatching](https://developer.apple.com/documentation/security/secitemcopymatching(_:_:)), [Apple no-prompt authentication behavior](https://developer.apple.com/documentation/security/ksecuseauthenticationuifail), [fal Kling model schema](https://fal.ai/models/fal-ai/kling-video/v2.5-turbo/pro/image-to-video/api).
