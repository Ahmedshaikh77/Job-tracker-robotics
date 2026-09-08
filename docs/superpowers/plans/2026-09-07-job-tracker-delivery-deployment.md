# Job Tracker Delivery and Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver verified strong matches immediately, moderate matches once daily, and deploy the repaired tracker safely through GitHub Actions and Telegram.

**Architecture:** Convert assessments into compact provenance-labeled alert records, then use two durable workflow phases. Prepare scans and queues without sending, merges that queue into the default branch, and only then delivery reloads the committed state and sends pending chunks. Each receipt is merged back separately. A dependency-injected orchestrator separates live, seed, validation, dry-run, isolated smoke-test, and explicitly confirmed recovery-delivery modes.

**Tech Stack:** Python 3.11, requests, zoneinfo, pytest, GitHub Actions, Telegram Bot API

**Spec:** `docs/superpowers/specs/2026-09-07-job-tracker-alerts-v2-design.md`

## Global Constraints

- Live mode fails before fetching when either Telegram secret is missing. Validation may call only `getMe` and `getChat`; it never calls `sendMessage`.
- Telegram uses at most three attempts, honors `Retry-After`, backs off from two seconds with jitter to a 60-second cap, and paces successful chat sends by at least one second.
- Message chunks use a conservative 3,900-character limit, split only at job boundaries, and preserve company, title, recommendation, and official application link.
- Apply Now and Strong revisions deliver immediately. Moderate revisions deliver after 7:30 PM America/New_York and remain queued for 30 days.
- A successful message chunk is persisted before the next chunk. A failed chunk and all later chunks remain pending.
- The single smoke-test message requires explicit confirmation immediately before the send. A manual recovery delivery while live is disabled also requires action-time confirmation of its exact queued run ID. After the user confirms activation, scheduled job alerts and digests send automatically without per-message confirmation.
- Scheduled runs request minutes 17 and 47. GitHub scheduling lag is reported separately from same-run delivery latency.
- Production deployment requires tests, internal-browser review, default-branch merge with `JOB_TRACKER_LIVE_ENABLED=false`, credential validation, complete required-source seed, one confirmed isolated smoke test, explicit activation, and observation of one scheduled run.

**Prerequisite:** Complete the foundation, source-adapter, and matching plans first.

---

### Task 1: Project assessments into safe Telegram message chunks

**Files:**
- Create: `src/alert_formatting.py`
- Create: `tests/test_alert_formatting.py`

**Interfaces:**
- Consumes: `JobAssessment`, candidate `first_seen_at`, and `AlertItem`
- Produces: `project_alert_item(assessment, candidate_state, queued_at, queued_run_id, fetch_completed_at)`, `format_alert_entry()`, `FormattingQuarantine`, `MessageChunk`, `ChunkBuildResult`, and `build_message_chunks()`

- [ ] **Step 1: Write failing projection and provenance tests**

Create an Apply Now assessment and assert every required display field:

```python
def test_projection_labels_facts_and_tracker_assessments(assessment, candidate_state):
    item = project_alert_item(
        assessment,
        candidate_state,
        queued_at="2026-09-07T20:00:00Z",
        queued_run_id="run-123",
        fetch_completed_at="2026-09-07T19:59:00Z",
    )
    text = format_alert_entry(item)
    assert "Apply Now | 92/100" in text
    assert "Salary [Confirmed: structured-feed]" in text
    assert "Experience [Confirmed: official-detail]" in text
    assert "Authorization [Tracker inference]" in text
    assert "Fit [Tracker assessment]" in text
    assert "Gap [Tracker assessment]" in text
    assert "CV [Tracker assessment]" in text
    assert assessment.resume_reason in text
    assert "https://company.test/jobs/123" in text


def test_unknown_posted_date_shows_first_seen(assessment, candidate_state):
    item = project_alert_item(
        assessment,
        candidate_state,
        queued_at="2026-09-07T20:00:00Z",
        queued_run_id="run-123",
        fetch_completed_at="2026-09-07T19:59:00Z",
    )
    text = format_alert_entry(replace(
        item,
        posted_date=AlertFact("Unknown", EvidenceStatus.NOT_PUBLISHED, FactSource.UNAVAILABLE),
    ))
    assert "Posted date [Not published]: Unknown" in text
    assert "First seen: 2026-09-07" in text
```

Assert projection preserves `assessment.reopen_generation`, `assessment.job.source_key`, the candidate's durable identity aliases, `queued_run_id`, and `fetch_completed_at` exactly. Empty run IDs or non-ISO timestamps are rejected before queueing because the durable same-run ledger depends on them.

Add a test proving HTML special characters are escaped in visible text and `href`, including quotation marks in a URL.

- [ ] **Step 2: Write failing chunk-boundary and truncation tests**

```python
def test_chunker_splits_only_between_jobs(alert_items):
    result = build_message_chunks(alert_items, heading="New strong matches", limit=3900)
    assert result.quarantines == ()
    chunks = result.chunks
    assert all(len(chunk.text) <= 3900 for chunk in chunks)
    assert tuple(rid for chunk in chunks for rid in chunk.revision_ids) == tuple(
        item.revision_id for item in alert_items
    )


def test_oversized_optional_fields_are_truncated_but_identity_survives(oversized_item):
    result = build_message_chunks([oversized_item], heading="New strong matches", limit=3900)
    assert result.quarantines == ()
    chunk = result.chunks[0]
    assert oversized_item.company in chunk.text
    assert oversized_item.title[:200] in chunk.text
    assert oversized_item.recommendation.value in chunk.text
    assert oversized_item.application_url in chunk.text
    assert len(chunk.text) <= 3900
```

Reject source records before projection when company exceeds 200 characters, title exceeds 300, URL exceeds 2,048, or `urlsplit()` does not show an absolute HTTPS URL with a nonempty hostname and no embedded username/password. Optional match reason and gap each truncate to 500 characters; location, experience, authorization, CV, and CV-selection evidence each truncate to 300. Add a mandatory-skeleton test that HTML-escapes the heading, company, title, recommendation, score, and official link before measuring. If that required skeleton alone exceeds 3,900 characters, return a typed `FormattingQuarantine` source error and do not queue or silently drop the legitimate candidate.

- [ ] **Step 3: Run formatter tests and verify failures**

```bash
python -m pytest tests/test_alert_formatting.py -v
```

Expected: formatter and message chunk types are absent.

- [ ] **Step 4: Implement the compact alert projection and format**

Create:

```python
@dataclass(frozen=True, slots=True)
class MessageChunk:
    text: str
    revision_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FormattingQuarantine:
    revision_id: str
    source_key: str
    code: str


@dataclass(frozen=True, slots=True)
class ChunkBuildResult:
    chunks: tuple[MessageChunk, ...]
    quarantines: tuple[FormattingQuarantine, ...]


def html_escape(value: str) -> str:
    return html.escape(value or "", quote=True)
```

Format each entry in this order: recommendation and score; company and title; location/work arrangement; posted date/first-seen; salary; required experience; authorization; full-time status; match reason; important gap; recommended CV; official application link. The five assessment-derived lines say `Tracker assessment`. Factual lines carry `Confirmed`, `Not published`, or `Unresolved` plus provenance.

`build_message_chunks()` always returns `ChunkBuildResult`. It formats each job first, places valid complete entries under one heading, and starts a new chunk before adding an entry that would exceed 3,900 escaped characters. If one entry is too large, shrink optional fields in the approved order and reformat it; never slice HTML arbitrarily. If the escaped mandatory skeleton cannot fit, append a sanitized quarantine with code `mandatory-skeleton-too-large`, continue building chunks for the other entries, and make the caller's run nonzero. During prepare, the orchestrator calls it with each projected prospective item before queue insertion; a quarantined item is not queued, while already valid items remain queueable. During delivery, any unexpected quarantine stops that queue before sending its chunks and reports a degraded nonzero result. No quarantine stores job description or user-controlled text.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/test_alert_formatting.py -v
git add src/alert_formatting.py tests/test_alert_formatting.py
git commit -m "feat: format provenance-aware job alerts"
```

Expected: all projection, escaping, truncation, and chunk tests pass.

---

### Task 2: Replace Telegram booleans with bounded, validated transport

**Files:**
- Replace: `src/alerts.py:1-90`
- Create: `tests/test_telegram.py`

**Interfaces:**
- Consumes: formatted text chunks
- Produces: `TelegramConfigurationError`, `TelegramTransientError`, `TelegramPermanentError`, `TelegramAuthError`, `TelegramReceipt`, `TelegramNotifier.from_env(environ, policy)`, `validate_credentials()`, and `send_message()`

- [ ] **Step 1: Write failing credential and validation tests**

```python
def test_live_notifier_requires_both_secrets():
    with pytest.raises(TelegramConfigurationError):
        TelegramNotifier.from_env({"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "123"})


def test_validation_calls_getme_and_getchat_but_never_sendmessage(fake_session):
    notifier = TelegramNotifier("token", "123", session=fake_session, sleep=lambda seconds: None)
    notifier.validate_credentials()
    paths = [call.url for call in fake_session.calls]
    assert paths == ["getMe", "getChat"]
    assert "sendMessage" not in paths
```

Assert missing credentials never become a dry-run success. Dry-run bypasses notifier construction; validation requires both secrets.

- [ ] **Step 2: Write failing retry, pacing, API-status, and redaction tests**

Cover timeout then success, 500 then success, 429 with `retry_after`, a nonretryable 400, fatal 401/403, and HTTP 200 with Telegram `{ "ok": false }`. Inject a monotonic clock, sleeper, and zero jitter. Assert exactly three total attempts, a minimum one-second interval between chat sends, and no token, chat ID, response body, or token-bearing URL in raised/logged text.

```python
def test_429_honors_retry_after(fake_session, fake_clock):
    fake_session.queue(429, {"ok": False, "parameters": {"retry_after": 4}})
    fake_session.queue(200, {"ok": True, "result": {"message_id": 9}})
    notifier = notifier_with(fake_session, fake_clock)
    receipt = notifier.send_message("hello")
    assert fake_clock.sleeps == [4]
    assert receipt.message_id == 9
```

- [ ] **Step 3: Run transport tests and verify current failures**

```bash
python -m pytest tests/test_telegram.py -v
```

Expected: current missing-secret, recursive retry, pacing, and receipt behavior fails.

- [ ] **Step 4: Implement iterative Telegram transport**

Create:

```python
@dataclass(frozen=True, slots=True)
class TelegramReceipt:
    message_id: int
    delivered_at: str


class TelegramNotifier:
    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        policy: TelegramPolicy = TelegramPolicy(),
    ) -> "TelegramNotifier":
        environ = os.environ if environ is None else environ
        token = environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            raise TelegramConfigurationError("Telegram credentials are missing")
        return cls(token, chat_id, policy=policy)
```

Use an iterative loop bounded by `policy.max_attempts`. Retry only timeout, connection failure, 429, 500, 502, 503, and 504. Honor integer or HTTP-date `Retry-After`; otherwise use the policy's exponential backoff and cap. Pace successful sends with `policy.minimum_send_interval_seconds`. Raise `TelegramAuthError` on 401 or 403, `TelegramPermanentError` on other 4xx or `{ok:false}`, and `TelegramTransientError` after the final transient attempt. `validate_credentials()` calls `getMe` then `getChat` and validates `{ok:true}` without calling `sendMessage`.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/test_telegram.py -v
git add src/alerts.py tests/test_telegram.py
git commit -m "feat: make Telegram delivery bounded and explicit"
```

Expected: all Telegram tests pass with no network access.

---

### Task 3: Deliver only durably persisted queues and acknowledge one chunk at a time

**Files:**
- Create: `src/delivery.py`
- Create: `tests/test_delivery.py`

**Interfaces:**
- Consumes: `AlertItem`, `PendingHealthSummary`, `MessageChunk`, persisted-state checks, queue-current/invalidation APIs, all pending/receipt state APIs, and `TelegramNotifier.send_message()`
- Produces: `chunk_id_for(heading, revision_ids)`, `DeliveryReport`, `deliver_pending(*, heading, queue_kind, notifier, state, now, revision_ids=None)`, and `deliver_health_pending(*, notifier, state, now, delivery_id=None)`

- [ ] **Step 1: Write failing durability, ordering, and partial-failure tests**

```python
def test_dirty_or_unpersisted_queue_is_never_sent(state, notifier):
    state.dirty = True
    report = deliver_pending(
        heading="Strong matches", queue_kind="immediate",
        notifier=notifier, state=state, now=NOW,
    )
    assert report.failed is True
    assert notifier.messages == []


def test_success_is_saved_before_next_chunk(persisted_state, notifier):
    deliver_pending(heading="Strong matches", queue_kind="immediate", notifier=notifier, state=persisted_state, now=NOW)
    assert persisted_state.events == [
        "send:chunk-1",
        "save:delivered:chunk-1",
        "send:chunk-2",
        "save:delivered:chunk-2",
    ]


def test_later_failure_leaves_only_unsent_revisions_pending(persisted_state, failing_notifier):
    report = deliver_pending(heading="Strong matches", queue_kind="immediate", notifier=failing_notifier, state=persisted_state, now=NOW)
    assert report.delivered_revision_ids == ("rev-1",)
    assert tuple(item.revision_id for item in persisted_state.pending_immediate()) == ("rev-2",)
    assert report.failed is True
```

Create the persisted-state fixture by queueing known `AlertItem` records, calling `save_atomic()`, and loading a new `StateManager` from that file. Add tests for retry runs skipping delivered revisions, `queue_kind="moderate"` consuming only `pending_moderate()`, fatal auth stopping later chunks, deterministic `chunk_id_for()` output, a state-save failure after a Telegram receipt returning nonzero, and simulation never counting as delivery. For the save-failure case, assert `delivered_revision_ids` excludes the unpersisted receipt and reloading the file still exposes that revision as pending. Add cancellation boundaries: cancellation after a queued state file exists but before the first send leaves every item for the next run; cancellation after a receipt but before receipt persistence may duplicate that chunk, which is the accepted at-least-once tradeoff, but never loses later chunks.

Add stale-queue tests for immediate and Moderate delivery. Before constructing chunks or the notifier, reject a missing candidate as state corruption. For an existing candidate that is closed, whose latest assessment is ineligible or Skip, whose `last_queued_revision_id` differs, or whose revision ID appears in the invalidation marker, call candidate-wide `invalidate_candidate_queue(..., DELIVERY_STALE, now)` and save the removals. Assert no invalid item reaches `send_message`; other current candidates may proceed only after that save succeeds. A source-filtered invalidation for source A must not block a surviving queue item from source B. A save failure stops all transport. Reloading state proves the invalid entry remains removed and a later delta can carry its tombstone.

Add health-summary delivery tests using a persisted `PendingHealthSummary`: dirty/unpersisted intent never sends; a receipt calls `mark_health_summary_delivered()` and saves; failure leaves intent pending; retry skips a delivered health ID. When `delivery_id` is supplied, an unknown ID fails closed and an exact pending ID is the only health intent sent. Health delivery is separate from job revision chunks and never invents a job revision ID.

- [ ] **Step 2: Run tests and verify coordinator is missing**

```bash
python -m pytest tests/test_delivery.py -v
```

Expected: delivery coordinator and report are absent.

- [ ] **Step 3: Implement the delivery transaction**

Create:

```python
@dataclass(frozen=True, slots=True)
class DeliveryReport:
    attempted_chunks: int
    delivered_chunks: int
    delivered_revision_ids: tuple[str, ...]
    pending_revision_ids: tuple[str, ...]
    failed: bool
    error: str | None
```

`queue_kind` is a `Literal["immediate", "moderate"]`. Both delivery functions refuse Telegram unless `state.is_persisted()` is true. `deliver_pending()` reads only the selected persisted job queue and filters delivered revisions defensively. Before notifier construction or chunk formatting, it calls `queue_item_is_current()` for every selected item. It fails closed on a missing candidate; for every other stale item it calls `invalidate_candidate_queue(..., DELIVERY_STALE, now)` and persists all removals before any network send. A stale-filter save failure stops transport. It then delivers only the still-current frozen subset. An optional ordered `revision_ids` subset lets a digest or recovery run freeze entries due at invocation time; unknown or wrong-queue IDs fail closed. `chunk_id_for()` is SHA-256 over the heading plus the ordered revision IDs, so a retry reconstructs the same identifier without depending on message text truncation. After each receipt it removes that chunk's revisions from both queues, records the receipt, and calls `save_atomic()` before continuing; only after that save succeeds does it append the revision IDs to `delivered_revision_ids`. `deliver_health_pending()` reads only persisted health-summary intent; optional `delivery_id` restricts it to that exact pending record and fails closed if absent. It sends the already-sanitized text, records the health delivery ID, and saves before reporting success. On any send or save exception either function stops and returns a failed report. Atomic-save failure leaves the on-disk file at the last successful checkpoint; callers must discard the dirty in-memory manager and reload that file before building a receipt delta. The API-success/save-failure window is therefore treated as an uncertain at-least-once delivery, not a durable receipt. Neither function queues new work nor marks a dry-run log or simulated response delivered. The orchestrator and workflow own queue creation and durable pre-send persistence.

- [ ] **Step 4: Run tests and commit**

```bash
python -m pytest tests/test_delivery.py -v
git add src/delivery.py tests/test_delivery.py
git commit -m "feat: persist chunk-level delivery receipts"
```

Expected: all transactional delivery tests pass.

---

### Task 4: Add the New York-time moderate digest and daily health summary

**Files:**
- Create: `src/digest.py`
- Modify: `src/state.py`
- Create: `tests/test_digest.py`

**Interfaces:**
- Consumes: moderate queue, source-health state, chunk delivery, and `ZoneInfo("America/New_York")`
- Produces: `digest_due()`, `run_moderate_digest()`, `health_summary_due()`, `prepare_health_summary()`, and `deliver_health_summary()`

- [ ] **Step 1: Write failing time-boundary, DST, empty-day, and retry tests**

```python
def test_digest_is_due_at_exactly_1930_new_york():
    now = datetime(2026, 9, 7, 19, 30, tzinfo=ZoneInfo("America/New_York"))
    assert digest_due(now, last_processed_date="2026-09-06") == date(2026, 9, 7)


def test_digest_is_not_due_before_1930():
    now = datetime(2026, 9, 7, 19, 29, tzinfo=ZoneInfo("America/New_York"))
    assert digest_due(now, last_processed_date="2026-09-06") is None


def test_empty_day_advances_without_sending(state, notifier):
    result = run_moderate_digest(state, notifier, NOW_AFTER_1930)
    assert result.completed_date == date(2026, 9, 7)
    assert notifier.messages == []
```

Add DST spring/fall cases, failed final chunk not advancing the date, successful earlier chunks being acknowledged, missed days combining retained entries, 30-day expiry, same-day no resend, post-completion entries waiting until the next day, and one daily high-priority source-health summary. For health, assert it is not due before 19:30 New York time, an empty eligible day advances its separate date without sending, and a nonempty summary first creates a deterministic persisted `PendingHealthSummary`. A failed send leaves that intent/date incomplete, and the same date never resends after success. When yesterday's health intent is still pending, today's prepare neither advances today's completion nor queues another summary; delivery retries the single oldest intent first, and today's summary can be prepared on the next run after that receipt is durable.

- [ ] **Step 2: Run tests and verify digest module is absent**

```bash
python -m pytest tests/test_digest.py -v
```

Expected: digest and daily health-summary functions are absent.

- [ ] **Step 3: Implement exact digest completion semantics**

Convert `now` to `America/New_York`; the due date is today's New York date only when local time is at least 19:30 and `last_processed_date` differs. Select undelivered Moderate entries queued before the current digest invocation and not older than 30 days. An empty selection calls `complete_digest(local_date, completed_at)` with the New York `YYYY-MM-DD` and aware UTC completion timestamp, then immediately calls `save_atomic()` without sending. Pass the frozen revision IDs to `deliver_pending(..., queue_kind="moderate", revision_ids=due_ids)`; call the same completion method only after every due revision is delivered, then call `save_atomic()` before returning. This atomically advances `last_processed_date` and `last_processed_at`. Earlier successful chunks remain acknowledged if a later chunk fails. Add an orchestration regression in which moderate completion dirties then persists state before a due health-summary delivery begins.

The health summary uses the separate `last_health_summary_date`/`last_health_summary_at` pair and the same 19:30 New York boundary. It includes enabled sources marked `priority: high` when their circuit is open/half-open or they have at least `persistent_health_warning_runs` consecutive incomplete/failed runs. `prepare_health_summary()` is part of prepare. If a health intent is already pending, it leaves that intent and both completion fields unchanged and does not create a second intent. Otherwise it calls `complete_health_summary(local_date, completed_at)` for an empty eligible day without sending, or builds deterministic ID `health:<local-date>:<content-hash>` and calls `queue_health_summary()` for a nonempty day. That one intent or empty-day completion therefore reaches the default branch before delivery. `deliver_health_summary()` is part of delivery: it begins only after moderate completion has been persisted, selects the single oldest pending health intent, calls `deliver_health_pending()` for that exact ID, and after a successful persisted receipt calls `complete_health_summary(item.local_date, completed_at)` and `save_atomic()` again. Failure leaves intent and completion pair incomplete for retry. Health messages never use or mutate job revision IDs. Moderate completion uses `complete_digest()` only after every due revision is delivered. All send functions begin with clean persisted state.

- [ ] **Step 4: Run tests and commit**

```bash
python -m pytest tests/test_digest.py -v
git add src/digest.py src/state.py tests/test_digest.py
git commit -m "feat: add daily moderate and health digests"
```

Expected: all timezone and digest tests pass.

---

### Task 5: Define and verify immutable state deltas before orchestration

**Files:**
- Create: `src/state_merge.py`
- Create: `tests/test_state_merge.py`
- Create: `tests/fixtures/noop_state_delta_v2.json`

**Interfaces:**
- Consumes: two valid version-2 state snapshots
- Produces: `DeltaMode`, `StateDelta`, `build_delta(base, final, run_id, mode, created_at)`, `apply_delta(remote, delta)`, atomic delta serialization, and a merge CLI

- [ ] **Step 1: Write failing delta round-trip and conflict tests**

Use this exact serialized shape:

```python
{
    "delta_version": 1,
    "state_schema_version": 2,
    "run_id": "run-123",
    "mode": "live-prepare",
    "created_at": "2026-09-07T20:00:00Z",
    "has_changes": True,
    "source_upserts": {},
    "candidate_upserts": {},
    "candidate_removals": {},
    "run_upserts": {},
    "run_removals": {},
    "pending_immediate_upserts": {},
    "pending_moderate_upserts": {},
    "pending_removals": {},
    "pending_health_upserts": {},
    "pending_health_removals": {},
    "delivered_health_upserts": {},
    "delivered_health_removals": {},
    "digest_completion": None,
    "health_summary_completion": None,
    "delivered_upserts": {},
    "delivered_removals": {},
    "meta_upserts": {},
}
```

`DeltaMode` has exactly `LIVE_PREPARE = "live-prepare"`, `LIVE_DELIVER = "live-deliver"`, `SEED = "seed"`, and `RECOVER_DELIVERY = "recover-delivery"`. Dry-run, validation, and smoke-test never produce a state delta. `build_delta(base: Mapping[str, Any], final: Mapping[str, Any], run_id: str, mode: DeltaMode, created_at: datetime) -> StateDelta` requires an aware `created_at` and serializes it in UTC. `StateDelta` validates every key in the shape above, rejects unknown keys, requires nonempty `run_id`, a UTC ISO-8601 `created_at`, and makes `has_changes` equal whether any mutation or completion field is nonempty.

`digest_completion` is either `None` or exactly `{"local_date": "YYYY-MM-DD", "completed_at": "<aware UTC ISO-8601>"}` and represents the foundation pair `last_processed_date` plus `last_processed_at`. `health_summary_completion` has the same exact object shape and represents `last_health_summary_date` plus `last_health_summary_at`. `build_delta()` emits an object only when its paired completion value advanced. `apply_delta()` passes each object through the foundation `set_completion_if_newer()` semantics so date and timestamp update atomically.

`pending_removals` is keyed by `"<queue-kind>:<revision-id>"`, where queue kind is exactly `immediate` or `moderate`. Each value has exact keys `queue_kind`, `revision_id`, `removed_at`, `reason`, and `replacement_revision_id`; `reason` is one of `delivered`, `promoted`, `superseded`, `invalidated`, or `expired`. This scope is required because Moderate-to-immediate promotion removes and inserts the same revision ID in one delta. The Moderate tombstone must not delete the immediate upsert. `pending_health_removals` is keyed by health `delivery_id`; each value has `delivery_id`, `removed_at`, and a `reason` of `delivered` or `expired`. `candidate_removals`, `run_removals`, `delivered_removals`, and `delivered_health_removals` are keyed by their respective identity and contain exact keys `removed_at` and `reason`; candidate/run/health-receipt reasons are `expired`, while job-receipt reason is `expired` or `over-cap`. These retention tombstones are necessary for the promised exact base-to-final round trip and must be empty in the no-op fixture.

`build_delta()` uses its immutable `created_at` as every `removed_at`, derives `promoted` only when that same revision moved from Moderate to immediate, derives `delivered` only when the matching receipt exists, derives `superseded` only when a replacement revision for the same candidate exists, and derives `invalidated` only when the final candidate's `last_queue_invalidation.revision_ids` contains that revision. It rejects any unexplained disappearance. A superseded tombstone names the replacement revision; a promoted tombstone names the same revision; the other reasons use `None`. It emits retention tombstones only for removals that the foundation `prune_state(..., now=created_at)` policy would make, including the delivered-count cap. `apply_delta()` compares every queue-scoped tombstone, including invalidation, with the target record's `record_updated_at`; an older or equal queue record is removed while a strictly newer requeue survives. It compares retention tombstones with each record's conflict clock, including the foundation run ledger's sole clock. It runs the same retention policy at `delta.created_at`, never wall-clock time, so replay is deterministic.

Assert `apply_delta(base, build_delta(base, final, ...)) == final`, replaying the same delta is idempotent, and invalid versions or missing required fields fail without modifying output. Store the exact all-empty shape above as `tests/fixtures/noop_state_delta_v2.json` with `has_changes: false`, `run_id: migration-rehearsal`, and `mode: seed`. Add an end-to-end test where the remote file is version 1 and that version-2 delta is applied through the real CLI; the merge path first applies the exact foundation migration, then the delta, and a second CLI replay produces byte-identical version-2 output. A pending-removal tombstone contains the exact queue-scoped fields defined above, including a nullable but always present `replacement_revision_id`; it wins over an older/equal upsert in that queue, while a strictly newer legitimate queue upsert wins. Prove older remote Moderate and health intents cannot resurrect after promotion, invalidation, or delivery, while a promotion cannot erase its immediate copy and a newer requeue after invalidation survives. Delivered identities and run-eligibility IDs union. Candidate and non-inventory source fields use `record_updated_at` and canonical-JSON hash as an equal-time tiebreaker. Source inventory uses `last_complete_at`; partial results never replace it. Digest and health completion values advance monotonically by local date, then completion timestamp, and never roll backward on out-of-order replay. Test each retention-removal field in the exact base-to-final round trip. Retention runs at `delta.created_at` after merge.

```python
def test_round_trip_and_replay_are_exact(base_state, final_state):
    delta = build_delta(base_state, final_state, "run-123", DeltaMode.LIVE_PREPARE, NOW)
    merged = apply_delta(base_state, delta)
    assert merged == final_state
    assert apply_delta(merged, delta) == final_state


def test_delivered_revision_cannot_be_resurrected_pending(remote, delivery_delta):
    merged = apply_delta(remote, delivery_delta)
    assert "rev-1" in merged["delivery"]["delivered"]
    assert "rev-1" not in merged["delivery"]["pending_immediate"]
    assert "rev-1" not in merged["digest"]["pending_moderate"]


def test_moderate_promotion_tombstone_does_not_delete_immediate_copy(base, promoted):
    delta = build_delta(base, promoted, "run-123", DeltaMode.LIVE_PREPARE, NOW)
    merged = apply_delta(base, delta)
    assert "rev-1" not in merged["digest"]["pending_moderate"]
    assert merged["delivery"]["pending_immediate"]["rev-1"] == (
        promoted["delivery"]["pending_immediate"]["rev-1"]
    )
```

- [ ] **Step 2: Run tests and verify the module is absent**

```bash
python -m pytest tests/test_state_merge.py -v
```

Expected: imports fail because the delta type, builder, replay, and CLI do not exist.

- [ ] **Step 3: Implement deterministic build, replay, and atomic output**

`build_delta()` compares semantic records and always returns a valid delta after a successful stateful phase. A no-change delta has `has_changes=False`, empty mutation fields, and still proves prepare completed safely; it does not create a state commit. It never treats routine fetch timestamps as changes, so raw round-trip equality remains valid because ignored timestamps were never assigned. `apply_delta()` accepts a valid version-1 or version-2 remote: it migrates v1 with the single foundation migration function, validates v2, resolves each job tombstone only against its named queue, resolves health tombstones against the health queue, applies candidate/run/job-receipt/health-receipt retention tombstones, unions receipts and per-run eligibility IDs, removes every delivered revision defensively from both job queues, advances completion dates monotonically, and prunes deterministically at `delta.created_at`. For one queue key, a tombstone wins over an upsert with an older or equal `record_updated_at`; a strictly newer upsert wins unless that revision is delivered. An existing delivered receipt wins over any pending value. Exact duplicate receipt replay is a no-op; two receipts for the same identity must agree on candidate ID, reopen generation, source key, queued run ID, fetch-completion time, and durable identity aliases, after which the earlier `delivered_at` wins with canonical-JSON hash as the equal-time tie breaker. A migrated legacy receipt may have `source_key=None`, but a live receipt may not. Health receipts follow the same exact-duplicate and earliest-delivery rule.

Candidate records and the non-inventory portion of each source record use `record_updated_at`, with canonical-JSON hash as an equal-time tie breaker. The source inventory group is exactly `active_ids`, `etag`, `fingerprint`, `source_total`, `total_is_authoritative`, and `last_complete_at`; that group is selected by later non-null `last_complete_at`, with canonical-JSON hash only when timestamps tie. A source upsert with no newer complete inventory may still merge its newer health, circuit, retry, warning, seed, and record fields without replacing the remote inventory group. Run ledgers union their sorted unique `eligible_immediate_revision_ids`, retain the earliest `started_at`, retain the latest `fetch_completed_at`, and set `record_updated_at` to the later input ledger clock. Digest and health-summary completion objects compare `local_date` first and `completed_at` second, never rolling either persisted pair backward. `meta_upserts` use their `record_updated_at` field. The CLI accepts `--state`, `--delta`, and `--output`, writes atomically only when the migrated or merged state changes, and exits nonzero without touching output for an absent, truncated, or invalid delta.

- [ ] **Step 4: Run tests and commit**

```bash
python -m pytest tests/test_state_merge.py -v
git add src/state_merge.py tests/test_state_merge.py tests/fixtures/noop_state_delta_v2.json
git commit -m "feat: add deterministic state deltas"
```

Expected: round-trip, replay, conflict, tombstone, and invalid-input tests pass.

---

### Task 6: Build the dependency-injected two-phase orchestrator and safe modes

**Files:**
- Create: `src/orchestrator.py`
- Create: `src/reporting.py`
- Replace: `src/main.py:1-145`
- Delete: `src/filters.py`
- Delete: `test_filter.py`
- Modify: `src/verify.py`
- Create: `tests/test_orchestrator.py`
- Create: `tests/test_reporting.py`
- Create: `tests/test_cli.py`
- Modify: `tests/test_config.py`
- Delete: `tests/test_legacy_filter_compat.py`

**Interfaces:**
- Consumes: typed settings, source registry, Stage 1, mandatory detail verification, evaluation, state, delta, delivery, and digest modules
- Produces: `RunMode`, `RunPhase`, `RunReport`, `JobTracker.run()`, `JobTracker.run_live_local()`, atomic `--delta-json` and `--report-json` outputs, environment-only smoke/recovery inputs, and `python -m src.reporting summarize --state --run-id --output`

- [ ] **Step 1: Write failing mode, order, and activation tests**

```python
def test_live_missing_credentials_fails_before_fetch_or_write(tracker):
    report = tracker.run(RunMode.LIVE, phase=RunPhase.PREPARE)
    assert report.exit_code == 2
    assert tracker.fetch_calls == []
    assert tracker.state.save_calls == 0


def test_dry_run_never_sends_or_mutates_production_state(tracker, production_bytes):
    report = tracker.run(RunMode.DRY_RUN)
    assert report.preview_items
    assert tracker.notifier.send_calls == []
    assert tracker.production_state_path.read_bytes() == production_bytes


def test_seed_is_credential_exempt_and_queues_nothing(tracker):
    report = tracker.run(RunMode.SEED)
    assert report.exit_code == 0
    assert tracker.notifier is None
    assert tracker.state.pending_immediate() == ()
    assert tracker.state.pending_moderate() == ()


def test_smoke_test_sends_exactly_one_payload_without_scan_or_state_change(
    tracker, production_bytes, monkeypatch
):
    monkeypatch.setenv("JOB_TRACKER_SMOKE_PAYLOAD", "approved exact payload")
    report = tracker.run(RunMode.SMOKE_TEST)
    assert tracker.fetch_calls == []
    assert tracker.notifier.messages == ["approved exact payload"]
    assert tracker.production_state_path.read_bytes() == production_bytes
    assert report.delivered == 1
```

Add `test_recover_delivery_requires_exact_confirmation_and_run_id`, `test_recover_delivery_filters_to_that_runs_immediate_items`, and `test_recover_delivery_never_scans_or_runs_digests`. Missing/wrong confirmation, missing run ID, no matching pending item, scheduled invocation, dirty state, incomplete seed, or a live-gate value other than the exact string `false` fails before notifier construction or transport. The successful test sets live to exactly `false`, queues and persists items from two run IDs, confirms one exact run ID, then proves only that run's immediate revisions are sent and a valid `recover-delivery` receipt delta is produced from an untouched base snapshot to the reloaded last-persisted temporary state.

Add `test_scheduled_live_is_blocked_until_activation`: scheduled live with `JOB_TRACKER_LIVE_ENABLED` absent or not exactly `true` exits cleanly before credentials, fetch, send, or state mutation. Manual validate, seed, dry-run, and smoke-test remain available while the gate is not true; guarded recover-delivery requires it to be exactly `false`. The CLI exposes one mutually exclusive `--mode` choice from `live|validate-only|dry-run|seed|smoke-test|recover-delivery`; `--phase prepare|deliver` is required only with live and rejected for every other mode. `--delta-json PATH` is required for live prepare, live delivery, seed, and recover-delivery, and rejected for dry-run, validation, and smoke-test. Stateful live and seed invocations require a nonempty `--run-id`; recover-delivery takes its run ID only from `JOB_TRACKER_RECOVERY_RUN_ID`, uses that exact value as the report and delta `run_id`, and rejects a conflicting `--run-id`. Every mode accepts `--report-json PATH`; it atomically writes a sanitized versioned report even when an operational phase exits nonzero. A report may set `delta_ready=true` only after the exact `--delta-json` file has been atomically written and schema-validated. Argument-validation failures occur before state or network access and write a failure report when a valid `--report-json` path was supplied.

Add deterministic pipeline tests: `begin_fetch()` is called on the orchestrator thread before workers; immutable results are applied in sorted source-key order; only the main thread mutates state; incomplete/shrunken results never produce candidates; every Stage-1 candidate receives adapter-specific `fetch_detail()` and only healthy detail reaches evaluation. Capture `source_was_unseeded` before applying an accepted complete snapshot and call `observe_candidate(..., discovered_during_seed=source_was_unseeded)`. Retrieve the candidate record after observation/detail mutation but before assessment mutation, pass that same snapshot to `evaluate_job(..., revision_policy=state.revision_policy)` and `should_alert_revision(..., policy=state.revision_policy)`, then call `record_assessment()`. A suppressed migrated equivalent calls `record_migration_baseline()` without queueing. Only when `source_was_unseeded` is false, queue Apply Now/Strong through `queue_immediate()`, Moderate through `queue_moderate()`, no Skip, and call `record_alert_basis()` after the queue method returns true. If evaluation is ineligible or Skip, call candidate-wide `invalidate_candidate_queue(..., ASSESSMENT_INELIGIBLE, now)` after recording it. For an existing `source_key|posting_id` whose new list row fails Stage 1, resolve it with `candidate_id_for_source_ref()` and call `invalidate_candidate_queue(..., STAGE_ONE_REJECTED, now, source_key=source_key)`; also clear a due detail retry. Core reconciliation and detail application apply the same source filter for each second-omission or official-close invalidation, then invalidate candidate-wide if every reference is closed. Add a cross-source regression proving a source-A dead link is removed while a valid source-B queued link survives. Accumulate only immediate revision IDs whose queue call returned true, then call `record_run_eligibility(run_id, revision_ids, fetch_completed_at, created_at)` once when that tuple is nonempty; `fetch_completed_at` is the latest UTC source/detail completion used by this prepare, while each `AlertItem` retains its own job-specific completion timestamp. Duplicate/no-op queues and Moderate entries never enter the same-run denominator. An unseeded source establishes immutable seed baselines and queues nothing from that first complete snapshot; after all of its rows are handled, call `mark_source_seeded(source_key, result.fetched_at)` so later material revisions can alert. This applies in live as well as seed mode, including nonrequired best-effort sources that recover after deployment. Detail failure schedules retry. A no-change prepare causes no dirty state or commit, but emits a valid `has_changes=False` delta so existing pending alerts and due digests can still reach delivery.

Add delivery-phase tests proving it refuses an uncommitted prepare result, reloads the committed state, sends existing immediate pending items, runs due moderate/health digests, and emits only receipt/completion changes. Any immediate delivery failure stops before Moderate or health delivery; any Moderate failure stops before health delivery. The phase still emits a delta containing only receipts and completion state that reached the last atomic save. `run_live_local()` must call `save_atomic()` after prepare and before the first send. Add cancellation tests across prepare save, queue-delta push, first send, and receipt save.

- [ ] **Step 2: Write exact same-run metric boundary tests**

The eligible denominator is the set of Apply Now and Strong revisions with `queued_run_id == current_run_id` first queued in that prepare run. A receipt counts only when it names one of those revisions, is durably persisted, and `0 <= delivered_at - fetch_completed_at <= 600`. Add exact tests for 95 of 100 passing, 94 of 100 failing, a zero denominator returning `None`, negative and late timestamps excluded, prior-run pending and Moderate entries excluded, and a Telegram receipt whose state save failed excluded. Report current pending-backlog count and oldest age separately. The workflow recalculates the final metric from default-branch state after receipt-delta sync; an unmerged receipt is not counted as durable success.

```python
def test_same_run_target_boundary():
    assert summarize_same_run(eligible=100, within_target=95).target_met is True
    assert summarize_same_run(eligible=100, within_target=94).target_met is False
    assert summarize_same_run(eligible=0, within_target=0).rate is None
```

- [ ] **Step 3: Run tests and verify current violations**

```bash
python -m pytest tests/test_orchestrator.py tests/test_cli.py tests/test_config.py -v
```

Expected: the current runner has no two-phase transaction, activation gate, isolated smoke mode, exact metrics, or typed composition.

- [ ] **Step 4: Implement exact modes, phases, and report**

```python
class RunMode(StrEnum):
    LIVE = "live"
    DRY_RUN = "dry-run"
    VALIDATE_ONLY = "validate-only"
    SEED = "seed"
    SMOKE_TEST = "smoke-test"
    RECOVER_DELIVERY = "recover-delivery"


class RunPhase(StrEnum):
    PREPARE = "prepare"
    DELIVER = "deliver"


@dataclass(frozen=True, slots=True)
class RunReport:
    report_version: int
    mode: RunMode
    phase: RunPhase | None
    exit_code: int
    phase_succeeded: bool
    run_id: str
    fetched: int
    assessed: int
    queued_immediate: int
    queued_moderate: int
    delivered: int
    eligible_immediate_revisions: int
    same_run_delivered_within_target: int
    same_run_delivery_rate: float | None
    same_run_target_met: bool | None
    fetch_completed_at: str | None
    last_delivery_at: str | None
    same_run_delivery_latency_seconds: float | None
    pending_backlog_count: int
    oldest_pending_age_seconds: float | None
    source_failures: tuple[str, ...]
    delivery_error: str | None
    delta_ready: bool
    delta_has_changes: bool
    preview_items: tuple[AlertItem, ...] = ()
```

`report_version` is exactly `1`. `JobTracker.run(mode, *, phase=None, event="manual", run_id=None)` is the only mode entry point; it reads environment-only live/smoke/recovery values through its injected environment mapping. A normal stateful run ID must match `^[A-Za-z0-9:._-]{1,200}$`. The same validator applies to the recovery target before it is used for selection, reporting, or artifact naming. The atomic report JSON has exact top-level fields `report_version`, `mode`, `phase`, `run_id`, `exit_code`, `phase_succeeded`, `delta_ready`, `delta_has_changes`, `counts`, `timing`, `same_run`, `backlog`, `source_failures`, and `delivery_error`. Internal `preview_items` are printed only for local dry-run output and are not serialized into the workflow report. The report contains no secret, description, Telegram response body, or smoke payload. `src.reporting summarize` reloads committed state and the run ledger, computes durable same-run metrics for the requested run ID, and writes the same versioned JSON shape for the workflow's final summary.

Validation checks typed config, required source schemas, and Telegram `getMe`/`getChat` without `sendMessage` or writes. Dry-run uses a clone and emits neither state nor delta. Seed is credential-exempt, evaluates baselines, marks each source seeded only after an accepted complete snapshot, queues nothing, and emits a delta. Smoke-test requires a nonempty `JOB_TRACKER_SMOKE_PAYLOAD` environment value, performs one `sendMessage`, and never scans or touches state; there is no shell payload argument. Add a hostile payload regression containing quotes, dollar signs, backticks, a newline, and `${{ ... }}`-like text, and prove the notifier receives the exact environment value without shell evaluation. Scheduled live checks the activation gate and programmatic source readiness before anything else; manual live also honors it after deployment. `recover-delivery` is manual-only and cannot accept a phase: it requires `JOB_TRACKER_LIVE_ENABLED` to equal exactly `false`, `JOB_TRACKER_RECOVERY_CONFIRMATION` to equal `SEND PENDING`, a valid nonempty `JOB_TRACKER_RECOVERY_RUN_ID`, clean persisted state, complete required-source seed markers, and valid Telegram credentials. It performs no fetch, evaluation, queue creation, digest, or health send. It copies the latest committed default-branch state to a unique temporary path, retains an untouched in-memory base, selects only pending-immediate items whose immutable `queued_run_id` equals that exact target, and sends that frozen subset through normal chunk delivery. Each receipt is saved to the temporary state before the next chunk. It then reloads and validates the last-persisted temporary state and builds a `DeltaMode.RECOVER_DELIVERY` delta from the untouched base to that snapshot, using the target queued run ID as the delta and report `run_id`. No matching pending item fails before notifier construction, creates no delta, and leaves the state copy unchanged. A partial send still emits the valid persisted receipts accumulated before failure so the workflow can synchronize them.

- [ ] **Step 5: Implement durable prepare and delivery phases**

Prepare loads a base snapshot and clones it. Before applying each accepted complete source result, it captures `source_was_unseeded = source_record.get("seeded_at") is None`. Applying that result first invalidates source-owned queues for every reference closed by a second accepted omission, including when another source keeps the candidate active. For each list row, Stage 1 runs before enrichment. If a rejected row matches an existing source reference, prepare invalidates only queued items owned by that source with `STAGE_ONE_REJECTED` and clears any due retry. For every listing that passes Stage 1, it calls `observe_candidate(..., discovered_during_seed=source_was_unseeded)` to establish the source reference, fetches and applies the adapter-specific detail result, and does not evaluate a closed or failed detail; an official detail close applies the same source-owned invalidation in the core API. For a healthy enriched detail, it retrieves one candidate snapshot after observation/detail mutation and before any assessment mutation; that same snapshot is passed to `evaluate_job(..., revision_policy=state.revision_policy)` and `should_alert_revision(..., policy=state.revision_policy)`. It then calls `record_assessment()`. An ineligible or Skip result invalidates all pending revisions for the candidate with `ASSESSMENT_INELIGIBLE`; an eligible newly alertable result queues by tier and records the alert basis only after successful queue insertion. Because `record_assessment()` initializes `seed_baseline` exactly once when the record was discovered during seed, a candidate first seen before its source has `seeded_at` cannot flood later activation. After all rows from an initially unseeded accepted complete result have been processed, it calls `mark_source_seeded(source_key, result.fetched_at)`; that first snapshot queues nothing even in live mode. After all accepted results, prepare calls `record_run_eligibility()` once for the nonempty tuple of newly queued immediate revisions and their common fetch-completion checkpoint. It prunes at the same `created_at` used by `build_delta()`, writes the temporary/local state atomically, and builds a delta from the untouched original base to the final state. On Actions it sends nothing.

Delivery loads the already committed post-prepare state from disk, retains an immutable base snapshot, and asserts the manager is clean. Each job-delivery call applies the defensive current-item gate and persists any stale invalidations before transport. It calls `deliver_pending()` for immediate items and continues to the Moderate digest only if that report succeeded; it continues to health delivery only if Moderate processing completed successfully. It never fetches jobs or creates queue entries. After delivery succeeds or fails, it discards the working manager, reloads the last atomically persisted temporary file, validates that state, and builds the receipt/completion or invalidation delta from the untouched base to that reloaded snapshot. An API receipt or invalidation whose state save failed therefore cannot leak into the delta or same-run numerator. `run_live_local()` composes prepare, successful atomic save, reload, and delivery so no send can precede durable queue storage.

Implement `assert_live_ready(settings, state)` to require `JOB_TRACKER_LIVE_ENABLED == "true"` and a committed `seeded_at` for every enabled `required_for_validation` source before prepare or delivery. This prevents an accidental variable toggle from activating partial coverage. Extend the configuration composition test to prove every YAML value reaches `HttpClient`, `StateLimits`, `SourceHealthPolicy`, `RevisionPolicy`, `TelegramNotifier`, formatter message limit, and digest timezone/time/persistence threshold. `load_config()` constructs both policies and every `StateManager.load()` call receives them explicitly. Defaults remain fallbacks only. Remove the legacy `JobFilter` shim and print-only root test now that `src.main` and `src.verify` use the new path.

- [ ] **Step 6: Run tests and commit**

```bash
python -m pytest tests/test_orchestrator.py tests/test_cli.py tests/test_reporting.py tests/test_config.py -v
python -m src.main --help
python -m src.reporting --help
git add src/orchestrator.py src/reporting.py src/main.py src/verify.py src/filters.py test_filter.py tests/test_orchestrator.py tests/test_cli.py tests/test_reporting.py tests/test_config.py tests/test_legacy_filter_compat.py
git commit -m "refactor: orchestrate durable tracker modes"
```

Expected: all mode, two-phase, no-change, metrics, and composition tests pass, and the CLI imports successfully.

---

### Task 7: Add the gated two-phase GitHub Actions workflow

**Files:**
- Replace: `.github/workflows/check_jobs.yml:1-65`
- Create: `tests/test_workflow.py`

**Interfaces:**
- Consumes: single-mode CLI, queue and receipt deltas, default-branch state, repository variables, Telegram secrets, and GitHub run metadata
- Produces: twice-hourly gated scans, durable queue-before-send delivery, three non-force state-push attempts, and truthful workflow timing

- [ ] **Step 1: Write failing workflow structure and activation tests**

Load YAML and normalize PyYAML's YAML-1.1 `True` key back to literal `"on"`. Assert schedule `17,47 * * * *`; one manual `mode` choice with options `live`, `validate-only`, `dry-run`, `seed`, `smoke-test`, and `recover-delivery`; string inputs `smoke_payload`, `recovery_run_id`, and `recovery_confirmation`; full-history checkout; `contents: write` and `actions: read`; tests before all operational phases; and one non-cancelling production concurrency group.

```python
def test_workflow_schedule_mode_and_concurrency(workflow):
    if "on" not in workflow and True in workflow:
        workflow["on"] = workflow.pop(True)
    assert workflow["on"]["schedule"] == [{"cron": "17,47 * * * *"}]
    assert workflow["on"]["workflow_dispatch"]["inputs"]["mode"]["type"] == "choice"
    assert workflow["concurrency"] == {
        "group": "job-tracker-production",
        "cancel-in-progress": False,
    }
```

Add tests proving scheduled and manual live runs check `vars.JOB_TRACKER_LIVE_ENABLED == 'true'` before credentials, source access, state changes, or Telegram. The repository variable is documented and initially created as `false`. Validate, dry-run, seed, and smoke-test remain runnable while live is disabled; recover-delivery is runnable only when the variable is exactly `false`. Live, seed, smoke-test, and recover-delivery must run from `github.event.repository.default_branch`; a dispatch from any other ref fails before credentials, state writes, or Telegram. Smoke-test and recover-delivery are manual-only. Recovery maps both inputs through environment variables to a static command, requires confirmation text `SEND PENDING`, and rejects a missing/nonmatching run ID before notifier construction or `sendMessage`. Telegram secrets are step-scoped only to live prepare/delivery, validation, smoke-test, and recover-delivery commands; checkout, tests, delta sync, artifact upload, and timing/report steps never receive them.

- [ ] **Step 2: Write failing two-phase state and status tests**

Assert the live path has this order: prepare scan to a temporary clone, validate queue delta, merge/push queue delta, reload the committed default-branch state, deliver, validate receipt delta, merge/push receipt delta. There is no `sendMessage`-capable command before successful queue push. Queue sync and receipt sync each retry a normal push at most three times after fetching the newest default branch and reapplying the same immutable delta; neither refetches jobs, resends Telegram, rebases generated state blindly, force-pushes, nor uses a dirty runner snapshot as delivery input. Final same-run metrics are recalculated from post-sync default-branch state, so an unmerged receipt cannot satisfy the delivery target.

Add tests for missing/invalid delta guards: synchronization runs only when a `delta_ready` output is true, the report and delta agree on `run_id`, mode, and `has_changes`, and schema validation passes. A scan/test/credential failure with no valid delta skips sync. A successful `has_changes=False` queue delta performs a validated no-op sync and still authorizes reload/delivery of backlog and due digests. A queue-sync failure blocks delivery, uploads the sanitized queue delta for guarded recovery, and makes the job nonzero. A receipt-sync failure overrides an otherwise successful send status, uploads the sanitized receipt delta, and preserves the original failure alongside the sync error in the summary. Recovery accepts only a `recover-delivery` receipt delta for the exact confirmed target run ID. No-change deltas create no commit.

Add `test_partial_delivery_receipts_sync_before_failure_exit`: chunk 1 sends and saves, chunk 2 fails, the delivery command emits a valid partial receipt delta, guarded receipt sync runs despite the nonzero delivery status, and chunk 1's receipt reaches the default branch before the original failure code is restored. Add degraded-prepare cases for a required-source failure and a formatting quarantine: if the phase produced a valid queue/health delta, sync it and deliver other already-verified good pending work, then preserve the degraded nonzero final status.

- [ ] **Step 3: Write failing timing and acceptance-summary tests**

Require the workflow to expose `GH_TOKEN: ${{ github.token }}` only to the read-only timing step, query the official current-run API using `gh api "repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"`, and read `created_at`, `run_started_at`, `event`, and `head_branch`. Query prior scheduled runs with `gh api "repos/$GITHUB_REPOSITORY/actions/workflows/check_jobs.yml/runs?event=schedule&branch=$DEFAULT_BRANCH&per_page=10"`, exclude the current run ID, and select the newest earlier run. When none exists, report `missed_slot_gap_count` as not applicable rather than zero. The summary reports:

- exact Actions queue lag: `run_started_at - created_at`
- estimated offset from the nearest requested minute 17/47, explicitly labeled estimate
- missed-slot gap count from the prior scheduled run
- fetch completion and fetch-to-delivery latency separately
- exact same-run numerator, denominator, rate, target result, pending backlog, and oldest pending age

Never call the estimated cron-slot offset exact schedule lag. Deployment validation also verifies `event == schedule`, the default branch, live activation, and the production concurrency group.

For a scheduled event, calculate `actions_queue_lag_seconds = run_started_at - created_at`. Calculate `estimated_slot_offset_seconds` from `created_at` to the most recent UTC minute 17 or 47 slot and label it estimated because GitHub does not expose the original scheduler dispatch timestamp. Compare the current and prior scheduled `created_at` values in 30-minute units; `missed_slot_gap_count = max(0, floor(interval_seconds / 1800) - 1)`. Keep all three fields separate from fetch-to-delivery latency.

- [ ] **Step 4: Run tests and verify the old workflow fails**

```bash
python -m pytest tests/test_workflow.py -v
```

Expected: the old workflow has no activation variable, single mode, two-phase durability, delta guard, or official run-timing report.

- [ ] **Step 5: Implement the workflow**

Use this trigger and permissions skeleton:

```yaml
on:
  schedule:
    - cron: '17,47 * * * *'
  workflow_dispatch:
    inputs:
      mode:
        type: choice
        options: [live, validate-only, dry-run, seed, smoke-test, recover-delivery]
        default: dry-run
      smoke_payload:
        type: string
        required: false
      recovery_run_id:
        type: string
        required: false
      recovery_confirmation:
        type: string
        required: false

permissions:
  contents: write
  actions: read

concurrency:
  group: job-tracker-production
  cancel-in-progress: false
```

Schedule selects live; dispatch selects exactly the input mode. Run the full suite first. Resolve `DEFAULT_BRANCH` only from `github.event.repository.default_branch`; before any stateful or message-sending command, require the checked-out ref to equal it. Define the normal run ID as `gha:${GITHUB_RUN_ID}:${GITHUB_RUN_ATTEMPT}` and pass that exact value through `--run-id` to live and seed phases. Live prepare copies production state to a unique runner-temporary path and invokes the static CLI with `--mode live --phase prepare`, `--state`, `--run-id`, `--delta-json`, and `--report-json`. A guarded sync helper validates the queue delta as `live-prepare`, validates the report/delta run ID and change flag, and replays it onto the newest default branch. A changed state is committed and pushed; a valid no-op is recorded without a commit. Either successful outcome authorizes delivery. Delivery refreshes the resulting default-branch state, copies it to a new unique temporary path, and invokes `--mode live --phase deliver` with distinct receipt-delta and report paths. It synchronizes that `live-deliver` receipt/completion delta even when a later chunk caused a nonzero delivery exit. Seed emits `DeltaMode.SEED` through the same guarded delta sync and never enters delivery. Validation and dry-run never accept or sync a delta. Smoke-test maps the dispatch input only through step environment `JOB_TRACKER_SMOKE_PAYLOAD: ${{ inputs.smoke_payload }}` and invokes a static command with no payload interpolation; prove a hostile input reaches Python unchanged and executes no shell text. It must record exactly one send with no state or scan.

Recover-delivery first refreshes the latest default branch and copies `state.json` to a unique temporary path. It maps `JOB_TRACKER_LIVE_ENABLED: ${{ vars.JOB_TRACKER_LIVE_ENABLED }}`, `JOB_TRACKER_RECOVERY_RUN_ID: ${{ inputs.recovery_run_id }}`, and `JOB_TRACKER_RECOVERY_CONFIRMATION: ${{ inputs.recovery_confirmation }}` only through the step environment, then invokes a static `--mode recover-delivery --state ... --delta-json ... --report-json ...` command with no `--run-id` or input interpolation. It performs only the guarded persisted-queue delivery described above. Its receipt sync validates `DeltaMode.RECOVER_DELIVERY` and the exact target run ID before replay. The workflow never uses an uncommitted or post-send working-tree file as the remote merge base.

Capture every phase exit code explicitly. Do not hide a primary failure under `if: always()` or a later successful command. On missing delta, preserve the primary result; on valid-delta sync failure, use a distinct nonzero final status. Receipt synchronization runs under a guarded `if: always()` only when the delivery report says `delta_ready=true`; after that attempt, restore the delivery failure code unless synchronization itself failed, in which case preserve both errors and use the distinct sync status. `python -m src.reporting summarize --state state.json --run-id "$RUN_ID" --output "$RUNNER_TEMP/final-report.json"` runs only after the latest committed state is reloaded. Upload only sanitized state-delta artifacts and never secrets or full job descriptions.

- [ ] **Step 6: Run tests and commit**

```bash
python -m pytest tests/test_workflow.py tests/test_state_merge.py tests/test_orchestrator.py -v
git add .github/workflows/check_jobs.yml tests/test_workflow.py
git commit -m "ci: schedule durable gated job scans"
```

Expected: workflow structure, activation, queue-before-send, retry, delta-guard, timing, and status-preservation tests pass.

---

### Task 8: Update documentation, run migration rehearsal, and deploy

**Files:**
- Replace: `README.md:1-191`
- Create: `tests/test_state_size.py`

**Interfaces:**
- Consumes: complete tracker and workflow
- Produces: user-facing operating documentation, tested state-size bound, reviewed GitHub deployment, and alert schedule activation

- [ ] **Step 1: Write the synthetic state-size test**

Build deterministic state with 25,000 active source IDs, 5,000 candidate revisions, and 10,000 delivered identities, serialize using production compact settings, and assert fewer than 5,000,000 bytes. Add a representative normal fixture and assert fewer than 2,000,000 bytes.

```python
def test_defined_synthetic_state_stays_below_five_mb(synthetic_state):
    payload = serialize_state(synthetic_state)
    assert len(payload) < 5_000_000
    assert b'"description"' not in payload
```

- [ ] **Step 2: Rewrite README with truthful behavior**

Document the exact 42-source roster plus disabled Apple, `Apply Now`/`Strong`/`Moderate` thresholds, confirmed-versus-inferred labels, role-to-CV routing, 30-day freshness gate, F-1 OPT and future-sponsorship uncertainty, Telegram setup, source-health meanings, Tesla best-effort limitation, the single mutually exclusive CLI mode selector, isolated smoke test, durable two-phase delivery, `JOB_TRACKER_LIVE_ENABLED`, 17/47 schedule, normal 15-to-45-minute expectation, possible GitHub delays, separate timing/latency metrics, 7:30 PM ET digest, state migration, rollback, and recovery steps. The guarded recovery procedure is exact: leave live disabled for a queue-sync failure; download the sanitized delta artifact in the internal browser; apply it to a fresh default-branch checkout with `python -m src.state_merge --state state.json --delta <downloaded-delta> --output <temporary-state>`; inspect the state diff; replace `state.json` only with that validated output; and make a normal non-force push. Immediately before any recovery send, obtain the user's explicit approval for the exact queued run ID, then dispatch manual `recover-delivery` with that run ID and confirmation `SEND PENDING`; it sends only that run's persisted immediate queue and never rescans. For a receipt-sync failure, apply the receipt delta before any retry so already-sent chunks are not intentionally resent; if it fully records the receipts, do not dispatch recovery delivery. Never apply a delta whose schema, base assumptions, or run ID cannot be verified. Explain that GitHub may disable scheduled workflows after 60 days with no repository activity and give the manual re-enable procedure. Remove every five-minute delivery promise and permanent auto-disable instruction.

- [ ] **Step 3: Run the full offline verification suite**

```bash
python -m pytest -q
python -m compileall -q src tests
python -m src.main --mode dry-run --state state.json
git diff --exit-code -- state.json
```

Expected: all tests pass, modules compile, dry-run produces no production-state diff, and no network call escapes the test suite.

- [ ] **Step 4: Rehearse version-1 migration without modifying production state**

```bash
TRACKER_TMP_DIR="$(mktemp -d)"
shasum -a 256 state.json > "$TRACKER_TMP_DIR/production-before.sha256"
cp state.json "$TRACKER_TMP_DIR/state-v1.json"
python -m src.state_merge --state "$TRACKER_TMP_DIR/state-v1.json" --delta tests/fixtures/noop_state_delta_v2.json --output "$TRACKER_TMP_DIR/state-v2.json"
python -m src.state_merge --state "$TRACKER_TMP_DIR/state-v2.json" --delta tests/fixtures/noop_state_delta_v2.json --output "$TRACKER_TMP_DIR/state-v2-replayed.json"
cmp "$TRACKER_TMP_DIR/state-v2.json" "$TRACKER_TMP_DIR/state-v2-replayed.json"
set +e
python -m src.health --state "$TRACKER_TMP_DIR/state-v2.json"
TRACKER_HEALTH_STATUS=$?
set -e
test "$TRACKER_HEALTH_STATUS" -eq 1
shasum -a 256 state.json > "$TRACKER_TMP_DIR/production-after.sha256"
cmp "$TRACKER_TMP_DIR/production-before.sha256" "$TRACKER_TMP_DIR/production-after.sha256"
```

Expected: the checksum files match, both merged outputs are byte-identical, and the real merge CLI exercised version-1 migration without a source or Telegram call. The health CLI returns exactly `1` because a migration rehearsal intentionally has no completed required-source seed; exit `2` would mean corruption and fails the rehearsal. Derive expected alerted and non-alerted counts by reading the copied version-1 input in the migration test, rather than hardcoding mutable counts. The temporary schema is version 2, every alerted identity has a recognized migration baseline, old nonmatching bulk records are absent, no descriptions persist, and no Telegram call occurs. Verify and document rollback: keep `JOB_TRACKER_LIVE_ENABLED=false`, restore the preceding default-branch workflow/state commit, and rerun validation before any reactivation.

- [ ] **Step 5: Commit documentation and final test**

```bash
git add README.md tests/test_state_size.py
git commit -m "docs: explain reliable job alerts"
```

- [ ] **Step 6: Review and push the implementation branch**

Run `git status --short`, `git log --oneline`, and `python -m pytest -q`. Open the branch diff and workflow in the Codex internal browser. Push `codex/job-tracker-alerts-v2` only after the full review is clean. Do not merge through an external browser.

- [ ] **Step 7: Merge behind the disabled gate, then validate and seed without sending**

In the internal browser, create or set the repository variable `JOB_TRACKER_LIVE_ENABLED=false` before merging. Merge the reviewed branch into the default branch, then dispatch `validate-only`. Confirm source-health output, `getMe` and `getChat` success, and zero `sendMessage` requests. Dispatch `seed` to migrate and establish baselines without alerts. Refuse activation until every `required_for_validation: true` source has a committed complete snapshot and per-source `seeded_at`; Foundation and Tesla remain visible nonrequired warnings when unhealthy.

- [ ] **Step 8: Stop for explicit live-message confirmation**

Present the exact proposed Telegram test payload to the user and wait for an explicit yes immediately before triggering manual `smoke-test`. Do not treat design, plan, merge, or seed approval as permission to send. Confirm that the run performed exactly one `sendMessage`, no source scan, and no state mutation.

- [ ] **Step 9: Enable and observe scheduled alerts**

After the confirmed smoke test succeeds, set `JOB_TRACKER_LIVE_ENABLED=true` in the internal browser. Observe scheduled runs there. Verify the event is `schedule`, the run is on the default branch under the production concurrency group, queue state commits before any send, and the summary separates exact Actions queue lag, estimated nearest-slot offset, missed-slot gaps, and fetch-to-delivery latency. Verify source health, fetched/assessed/queued/delivered counts, backlog, and no no-change state commit. Do not close deployment until at least one scheduled run with a nonzero newly eligible Apply Now/Strong denominator meets the 95 percent within-600-seconds target; a zero-denominator run is correctly reported as not applicable, not as a pass.

## Final Verification Gate

Run:

```bash
python -m pytest -q
python -m compileall -q src tests
git status --short
```

Expected: all tests pass, compilation succeeds, the worktree is clean, validation and complete required-source seed succeeded without a message, the user-confirmed isolated smoke test succeeded, activation is explicit, and a nonempty scheduled run meets the 95 percent target in the internal browser.
