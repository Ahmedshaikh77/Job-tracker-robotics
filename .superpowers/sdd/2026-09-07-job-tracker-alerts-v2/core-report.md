# Core implementation report

## Implementation

- Added the frozen domain model contract, explicit JSON serializers, validation, stable hashes, salary annualization, assessment invariants, and description-free alert records.
- Added state schema version 2 with strict version 1 migration, hard corruption failures, canonical persistence fingerprints, compact queues and receipts, atomic writes, retention, run ledgers, and monotonic digest checkpoints.
- Added conservative canonical identity rules, active URL merging, trusted requisition merging, source-scoped local identities, legacy migration bridging, and probable duplicate hints.
- Added source health, circuit recovery, collapse protection, accepted omission closure, official detail closure, retry scheduling, per-source seed markers, candidate reopen generations, compact assessment history, queue invalidation markers, and revision comparison baselines.

## Red and green evidence

- Model red: collection failed because `EmploymentType` and the new typed contracts did not exist.
- Model green: `tests/test_models.py`, 20 passed.
- State red: collection failed because `StateCorruptionError` and `src.dedupe` did not exist.
- State green: migration, persistence, queue, and migration dedupe tests, 13 passed.
- Lifecycle red: collection failed because `src.lifecycle` did not exist.
- Final core green: 45 tests passed across models, migration, persistence, dedupe migration, source lifecycle, and canonical dedupe.
- Syntax verification: all five owned core modules compiled successfully.

## Contracts and practical decisions

- `StateManager.load(path, limits=StateLimits(), *, health_policy=SourceHealthPolicy(), revision_policy=RevisionPolicy())` is the policy injection boundary.
- List observation hashes remain in `candidate.last_content_hash`. Full official detail hashes remain in `source_ref.last_detail_hash`, preventing list and detail payload differences from causing recurring state churn.
- Compact assessment snapshots use nested `required_experience`, `location`, and `salary` objects plus `eligible`, recommendation, authorization, compensation status, score, reopen generation, and assessment time. They never store description text.
- The plan's sample probable-duplicate test reused the same official URL while expecting different canonical identities. The implemented regression uses distinct URLs, because identical active official URLs are intentionally canonical across sources.
- When a URL belongs only to a closed candidate and no durable source or trusted requisition alias matches, resolution chooses the new source-scoped alias. This prevents a repost at a reused URL from overwriting the closed candidate record.
