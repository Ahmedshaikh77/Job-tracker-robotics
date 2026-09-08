"""Two-phase scan/queue and persisted delivery orchestration."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import os
from pathlib import Path
import re
import tempfile

from .alerts import TelegramNotifier
from .reporting import RunReport, metrics_from_state


class RunMode(StrEnum):
    LIVE = 'live'
    DRY_RUN = 'dry-run'
    VALIDATE_ONLY = 'validate-only'
    SEED = 'seed'
    SMOKE_TEST = 'smoke-test'
    RECOVER_DELIVERY = 'recover-delivery'


class RunPhase(StrEnum):
    PREPARE = 'prepare'
    DELIVER = 'deliver'


def valid_run_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9:._-]{1,200}', value) is not None


def _iso(now):
    return now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def runtime_policies(settings):
    from .state import StateLimits
    from .source_health import SourceHealthPolicy
    from .lifecycle import RevisionPolicy
    return (StateLimits(**asdict(settings.state_policy)),
            SourceHealthPolicy(failure_threshold=settings.fetch_policy.circuit_failure_threshold,
                               probe_interval=timedelta(hours=settings.fetch_policy.half_open_probe_hours),
                               shrink_ratio=settings.fetch_policy.shrink_ratio,
                               shrink_min_previous_count=settings.fetch_policy.shrink_min_previous_count),
            RevisionPolicy(same_id_reopen_days=settings.matching_policy.same_id_reopen_days,
                           compensation_material_change_ratio=settings.matching_policy.compensation_material_change_ratio,
                           compensation_threshold=settings.matching_policy.compensation_threshold))


class JobTracker:
    def __init__(self, *, settings, profile, state_path, environ=None, notifier_factory=None,
                 fetcher_factory=None, now=lambda: datetime.now(timezone.utc), delta_path=None):
        self.settings = settings
        self.profile = profile
        self.state_path = Path(state_path)
        self.environ = os.environ if environ is None else environ
        self.now = now
        self.delta_path = Path(delta_path) if delta_path else None
        self.notifier_factory = notifier_factory or (lambda: TelegramNotifier.from_env(self.environ, self.settings.telegram))
        self.fetcher_factory = fetcher_factory or self._fetcher

    def _fetcher(self, name):
        from .fetchers import get_fetcher
        from .fetchers.http import HttpClient, RetryPolicy
        policy = self.settings.fetch_policy
        http = HttpClient(retry=RetryPolicy(attempts=policy.max_attempts,
            connect_timeout=policy.connect_timeout_seconds, read_timeout=policy.read_timeout_seconds,
            backoff_base=policy.backoff_base_seconds, backoff_cap=policy.backoff_cap_seconds,
            per_host_pacing=float(policy.per_host_pacing_seconds)))
        return get_fetcher(name, http=http)

    def _load_state(self, path=None):
        from .state import StateManager
        limits, health, revision = runtime_policies(self.settings)
        return StateManager.load(path or self.state_path, limits, health_policy=health, revision_policy=revision)

    def _credentials_present(self):
        return bool(self.environ.get('TELEGRAM_BOT_TOKEN') and self.environ.get('TELEGRAM_CHAT_ID'))

    def _seed_ready(self, state):
        from .fetchers import source_key
        return all(state.state['sources'].get(source_key(s.as_fetcher_mapping()), {}).get('seeded_at')
                   for s in self.settings.companies if s.enabled and s.required_for_validation)

    def _emit_delta(self, base, state, report, mode):
        from .state import atomic_write_json
        from .state_merge import build_delta, DeltaMode
        if self.delta_path is None:
            return report
        delta = build_delta(base, state.state, report.run_id, DeltaMode(mode), self.now())
        atomic_write_json(self.delta_path, delta.to_dict())
        return replace(report, delta_ready=True, delta_has_changes=delta.has_changes)

    def run(self, mode, *, phase=None, event='manual', run_id=None):
        mode = RunMode(mode)
        phase = RunPhase(phase) if phase else None
        report = RunReport(mode=mode.value, phase=phase.value if phase else None, run_id=run_id or '')

        def failure(code):
            return replace(report, exit_code=2, phase_succeeded=False, delivery_error=code)

        if (mode is RunMode.LIVE) != (phase is not None):
            return failure('invalid-mode-phase')
        if mode in (RunMode.LIVE, RunMode.SEED) and not valid_run_id(run_id):
            return failure('invalid-run-id')
        if mode is RunMode.LIVE and self.environ.get('JOB_TRACKER_LIVE_ENABLED') != 'true':
            return replace(report, delivery_error='live-disabled')
        if mode in (RunMode.SMOKE_TEST, RunMode.RECOVER_DELIVERY) and event not in ('manual','workflow_dispatch'):
            return failure('manual-event-required')
        if mode is RunMode.RECOVER_DELIVERY:
            recovery_id = self.environ.get('JOB_TRACKER_RECOVERY_RUN_ID')
            if (self.environ.get('JOB_TRACKER_LIVE_ENABLED') != 'false'
                    or self.environ.get('JOB_TRACKER_RECOVERY_CONFIRMATION') != 'SEND PENDING'
                    or not valid_run_id(recovery_id) or (run_id and run_id != recovery_id)):
                return failure('invalid-recovery-confirmation')
            report = replace(report, run_id=recovery_id)
        if mode not in (RunMode.SEED, RunMode.DRY_RUN) and not self._credentials_present():
            return failure('telegram-credentials-missing')
        if mode is RunMode.SMOKE_TEST:
            payload = self.environ.get('JOB_TRACKER_SMOKE_PAYLOAD', '')
            if not payload:
                return failure('smoke-payload-missing')
            try:
                self.notifier_factory().send_message(payload)
                return replace(report, delivered=1)
            except Exception:
                return failure('smoke-delivery-failed')
        try:
            state = self._load_state()
            if mode in (RunMode.LIVE, RunMode.RECOVER_DELIVERY) and not self._seed_ready(state):
                return failure('required-source-seed-incomplete')
            if mode in (RunMode.LIVE, RunMode.RECOVER_DELIVERY) and not state.is_persisted():
                return failure('state-not-durably-persisted')
            base = deepcopy(state.state)
            if mode in (RunMode.DRY_RUN, RunMode.VALIDATE_ONLY):
                if mode is RunMode.VALIDATE_ONLY:
                    self.notifier_factory().validate_credentials()
                with tempfile.TemporaryDirectory(prefix='job-tracker-preview-') as folder:
                    state.path = Path(folder) / 'state.json'
                    return self._prepare(state, report, mode, validation=mode is RunMode.VALIDATE_ONLY)
            if mode is RunMode.SEED or (mode is RunMode.LIVE and phase is RunPhase.PREPARE):
                report = self._prepare(state, report, mode)
                state.prune(self.now())
                state.save_atomic()
                return self._emit_delta(base, state, report, 'seed' if mode is RunMode.SEED else 'live-prepare')
            if mode is RunMode.RECOVER_DELIVERY:
                due = tuple(item.revision_id for item in state.pending_immediate() if item.queued_run_id == report.run_id)
                if not due:
                    return failure('recovery-queue-empty')
                report = self._deliver(state, report, recovery_ids=due)
                mode_name = 'recover-delivery'
            else:
                report = self._deliver(state, report)
                mode_name = 'live-deliver'
            # Only disk-persisted checkpoints may be included after a partial failure.
            state = self._load_state()
            report = replace(report, **metrics_from_state(state.state, report.run_id, self.now()))
            return self._emit_delta(base, state, report, mode_name)
        except Exception:
            return failure('tracker-phase-failed')

    def _prepare(self, state, report, mode, validation=False):
        from .fetchers import source_key
        from .models import FetchResult, FetchHealth, FetchContext, DetailResult, DetailStatus, Recommendation, QueueInvalidationReason
        from .eligibility import stage_one, StageOneStatus
        from .evaluation import evaluate_job
        from .lifecycle import should_alert_revision, is_migration_equivalent
        from .alert_formatting import project_alert_item, build_message_chunks
        from .digest import prepare_health_summary

        sources = sorted((s for s in self.settings.companies if s.enabled), key=lambda s: source_key(s.as_fetcher_mapping()))
        scans, failures, required_failure, detail_tasks = [], [], False, []
        immediate_ids, previews = [], []
        fetched = assessed = qi = qm = 0
        completions = []

        def fetch(source, context):
            try:
                return self.fetcher_factory(source.fetcher).fetch(source.as_fetcher_mapping(), context)
            except Exception:
                return FetchResult(jobs=(), health=FetchHealth.FAILED, complete=False, active_ids=frozenset(),
                                   source_total=None, pages_fetched=0, fetched_at=_iso(self.now()), error='source-fetch-failed')

        with ThreadPoolExecutor(max_workers=self.settings.max_workers) as pool:
            for source in sources:
                key = source_key(source.as_fetcher_mapping())
                if not validation and not state.begin_fetch(key, self.now()):
                    continue
                context = FetchContext() if validation else state.fetch_context(key, self.now())
                scans.append((source, pool.submit(fetch, source, context)))
            for source, future in scans:
                key = source_key(source.as_fetcher_mapping())
                result = future.result()
                fetched += len(result.jobs)
                completions.append(result.fetched_at)
                source_unseeded = not state.state['sources'].get(key, {}).get('seeded_at')
                health = result.health
                if not validation:
                    transition = state.apply_fetch_result(key, result, self.now())
                    health = transition.health
                accepted = result.complete and health in (FetchHealth.HEALTHY, FetchHealth.EMPTY_VALID)
                if not accepted:
                    failures.append(key)
                    required_failure |= source.required_for_validation
                    continue
                if validation:
                    continue
                for job in sorted(result.jobs, key=lambda job: job.job_id):
                    if stage_one(job).status is StageOneStatus.REJECT:
                        existing = state.candidate_id_for_source_ref(key, job.job_id)
                        if existing:
                            state.invalidate_candidate_queue(existing, QueueInvalidationReason.STAGE_ONE_REJECTED, self.now(), source_key=key)
                        state.cancel_detail_retry(key, job.job_id, self.now())
                        continue
                    candidate_id, _, _ = state.observe_candidate(job, self.now(), discovered_during_seed=source_unseeded)
                    detail_tasks.append((source, source_unseeded, candidate_id, job))

            def detail(task):
                source, unseeded, cid, job = task
                try:
                    return self.fetcher_factory(source.fetcher).fetch_detail(source.as_fetcher_mapping(), job)
                except Exception:
                    return DetailResult(status=DetailStatus.FAILED, job=None, fetched_at=_iso(self.now()), error='detail-fetch-failed')

            details = [(task, pool.submit(detail, task)) for task in detail_tasks]
            for (source, unseeded, cid, job), future in details:
                result = future.result()
                state.apply_detail_result(cid, job.source_key, job.job_id, result, self.now())
                completions.append(result.fetched_at)
                if result.status is not DetailStatus.HEALTHY:
                    if result.status is DetailStatus.FAILED:
                        failures.append(job.source_key + ':detail')
                    continue
                candidate = deepcopy(state.candidate(cid))
                assessment = evaluate_job(result.job, cid, candidate, self.profile, self.now(),
                                           revision_policy=state.revision_policy,
                                           freshness_days=self.settings.matching_policy.freshness_days)
                alertable = should_alert_revision(candidate, assessment, self.now(), policy=state.revision_policy)
                state.record_assessment(assessment, _iso(self.now()))
                assessed += 1
                if not assessment.eligible or assessment.recommendation is Recommendation.SKIP:
                    state.invalidate_candidate_queue(cid, QueueInvalidationReason.ASSESSMENT_INELIGIBLE, self.now())
                    continue
                if candidate.get('migration_baseline_pending') and is_migration_equivalent(candidate, assessment):
                    state.record_migration_baseline(assessment, _iso(self.now()))
                    continue
                item = project_alert_item(assessment, candidate, _iso(self.now()), report.run_id or 'preview', result.fetched_at)
                formatted = build_message_chunks([item], heading='New strong matches', limit=self.settings.telegram.message_limit)
                if formatted.quarantines:
                    failures.append(job.source_key + ':formatting')
                    required_failure = True
                    continue
                if mode is RunMode.DRY_RUN:
                    previews.append(item)
                if mode is RunMode.SEED or unseeded or not alertable:
                    continue
                if assessment.recommendation in (Recommendation.APPLY_NOW, Recommendation.STRONG):
                    queued = state.queue_immediate(item)
                    if queued:
                        qi += 1
                        immediate_ids.append(item.revision_id)
                else:
                    queued = state.queue_moderate(item)
                    qm += int(queued)
                if queued:
                    state.record_alert_basis(assessment, item.queued_at)

        if not validation:
            for source, future in scans:
                key = source_key(source.as_fetcher_mapping())
                result = future.result()
                record = state.state['sources'].get(key, {})
                if result.complete and record.get('health') in ('healthy','empty-valid') and record.get('last_complete_at'):
                    state.mark_source_seeded(key, result.fetched_at)
            if immediate_ids:
                state.record_run_eligibility(report.run_id, immediate_ids, max(completions), _iso(self.now()))
            if mode is RunMode.LIVE:
                prepare_health_summary(state, self.settings, self.now())
        return replace(report, fetched=fetched, assessed=assessed, queued_immediate=qi, queued_moderate=qm,
                       fetch_completed_at=max(completions) if completions else None,
                       source_failures=tuple(sorted(set(failures))), preview_items=tuple(previews),
                       exit_code=1 if required_failure else 0, phase_succeeded=not required_failure)

    def _deliver(self, state, report, recovery_ids=None):
        from .delivery import deliver_pending
        from .digest import run_moderate_digest, deliver_health_summary
        notifier = self.notifier_factory()
        delivery = deliver_pending(heading='New strong matches', queue_kind='immediate', notifier=notifier,
                    state=state, now=self.now(), revision_ids=recovery_ids, message_limit=self.settings.telegram.message_limit)
        delivered = len(delivery.delivered_revision_ids)
        if delivery.failed:
            return replace(report, delivered=delivered, exit_code=1, phase_succeeded=False, delivery_error=delivery.error)
        if recovery_ids is not None:
            return replace(report, delivered=delivered)
        moderate = run_moderate_digest(state, notifier, self.now(), policy=self.settings.digest,
                                       message_limit=self.settings.telegram.message_limit)
        if moderate.delivery:
            delivered += len(moderate.delivery.delivered_revision_ids)
        if moderate.failed:
            return replace(report, delivered=delivered, exit_code=1, phase_succeeded=False, delivery_error='moderate-digest-failed')
        health = deliver_health_summary(state, notifier, self.now())
        return replace(report, delivered=delivered, exit_code=int(health.failed), phase_succeeded=not health.failed,
                       delivery_error='health-summary-failed' if health.failed else None)

    def run_live_local(self, *, run_id):
        prepared = self.run(RunMode.LIVE, phase=RunPhase.PREPARE, run_id=run_id)
        if prepared.exit_code == 2 or prepared.delivery_error == 'live-disabled':
            return prepared
        delivered = self.run(RunMode.LIVE, phase=RunPhase.DELIVER, run_id=run_id)
        return replace(delivered, exit_code=max(prepared.exit_code, delivered.exit_code),
                       phase_succeeded=prepared.phase_succeeded and delivered.phase_succeeded)
