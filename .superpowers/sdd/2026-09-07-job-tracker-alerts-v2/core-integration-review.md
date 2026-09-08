# Core Integration Review

Reviewed `src/orchestrator.py`, `src/workflow_tools.py`, `src/main.py`, `src/delivery.py`, `src/digest.py`, `.github/workflows/check_jobs.yml`, and their integration tests against the state and delta contracts.

## Resolved during review

- Recovery now fetches the latest default-branch `state.json`, validates it, and requires that refreshed path before any recovery send.
- A durable health-summary receipt now repairs a missing completion marker on the next prepare without sending the summary again.
- Delta construction, replay, workflow synchronization, and the recovery CLI now use the configured `StateLimits`. Custom shorter and longer pending-retention policies have regression coverage.
- `validate-only` now checks Telegram credentials, list-fetch schemas, and one actual detail response per accepted nonempty source without candidate or production-state mutation. A failed or closed required-source detail sample makes validation nonzero.

## Verdict

- No blocking orchestration, workflow, state, or delta contract issue remains in the reviewed scope.

## Verification evidence

- `tests/test_state_merge.py`: 11 passed.
- Integrated state, pipeline, CLI, workflow, Git sync, and digest selection: 40 passed.
- Queue persistence precedes transport, partial durable receipts remain delta-eligible, recovery is restricted to the exact queued run, and stale candidate queues are checked before `send_message`.
