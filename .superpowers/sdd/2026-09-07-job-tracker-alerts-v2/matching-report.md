# Matching implementation report

## Implementation

- Replaced the legacy boolean filter with typed Stage 1 decisions for approved title families, seniority rejection, structured United States location checks, and authoritative full-time evidence.
- Added typed official-detail section parsing plus experience, authorization, compensation, qualification, freshness, material-revision, scoring, and evaluation modules.
- Added a strictly validated immutable candidate profile and the versioned 100-point scoring policy in `profile.yaml`.
- Added exact role-to-resume routing with contextual precedence and no generic fallback. The robotics sensing route uses the provided file `Muhammad Ahmed Robotics Sensing Integration Engineer Tesla.pdf`.
- Removed the obsolete root print-only test and the temporary legacy `src.filters` compatibility shim after callers moved to the typed eligibility API.

## Red and green evidence

- Stage 1 red: test collection failed because `src.eligibility` did not exist. Stage 1 and temporary compatibility tests then passed, 61 tests.
- Parsing red: test collection failed because the parser, experience, authorization, and compensation modules did not exist. Their first green run passed 55 tests.
- Freshness red: test collection failed because `src.freshness` did not exist. The complete parsing and policy group then passed 65 tests.
- Profile and scoring red: test collection failed because `src.profile`, `src.qualifications`, and `src.scoring` did not exist. Their green run passed 49 tests.
- Resume routing red: test collection failed because `src.resumes` did not exist. Its green run passed 23 tests.
- Evaluation red: test collection failed because `src.evaluation` did not exist. Its green run passed 19 tests.
- Edge-case red: 10 regressions failed for United States location variants, structured location components, authorization phrases, configurable salary caps, profile-derived degree matching, non-USD material revisions, and empty resume evidence. The focused green run passed 146 tests.
- Authorization precedence red: 6 regressions failed for mixed positive sponsorship and personal restrictions plus common sponsorship, permanent-residency, and green-card blockers. The focused green run passed 26 tests.
- Final eligibility audit red: 14 regressions failed for additional authorization restrictions, common qualification headings, multiple required-year statements, disallowed title levels, negated full-time text, and employee-benefit boilerplate. The focused green run passed 126 tests.
- Final owned matching suite: 246 tests passed.

```text
.venv/bin/python -m pytest tests/test_stage_one.py tests/test_parsing.py tests/test_experience.py tests/test_authorization.py tests/test_freshness.py tests/test_compensation.py tests/test_profile.py tests/test_qualifications.py tests/test_scoring.py tests/test_resume_routing.py tests/test_evaluation.py -q
```

## Contracts and practical decisions

- Employer facts are read only from structured-feed or official-detail provenance. Tracker inference remains explicit, and an untagged description cannot establish employment, experience, authorization, compensation, or qualification facts.
- Eligibility requires an approved non-senior title, confirmed United States location, authoritative full-time employment, a permissible zero-to-three-year requirement or approved early-career unresolved case, no authorization block, a fresh or material revision, and an exact resume route.
- Personal citizenship, clearance, U.S.-person, ITAR, green-card, permanent-residency, and incompatible sponsorship restrictions take precedence over generic positive sponsorship language in the same posting.
- `Basic Qualifications` and plain `Qualifications` are mandatory sections. When multiple mandatory numeric experience statements are published, the strictest minimum controls eligibility rather than the first number encountered.
- Engineer III, Engineer IV, Engineer 3, Engineer 4, and mid-level titles are rejected at Stage 1. Official prose confirms full-time status only through a direct job-type statement; negation is rejected and employee-benefit boilerplate remains unresolved.
- `RevisionPolicy` is injected into `assess_material_revision` and `evaluate_job`; compensation revision thresholds are not duplicated in the profile. `freshness_days` is separately injected into `evaluate_job` from runtime matching policy.
- Required qualification matching is derived from typed candidate profile evidence. Missing required groups reduce the score and are surfaced as the important gap.
- The evaluator preserves the observed candidate identity and reopen generation supplied by state and performs no persistence or delivery side effects.

## Public matching API

- `stage_one(job: Job) -> StageOneDecision`
- `classify_role_family(title: str) -> tuple[str | None, tuple[str, ...]]`
- `refine_role_family(job: Job, initial_family: str | None, matched_title_signals: tuple[str, ...]) -> tuple[str | None, tuple[str, ...]]`
- `is_us_location(job: Job) -> bool`
- `resolve_employment(job: Job) -> tuple[EmploymentType, FactSource]`
- `split_job_sections(text: str) -> JobSections`
- `parse_experience(sections: JobSections, *, title: str, provenance: FactSource) -> ExperienceRequirement`
- `classify_authorization(text: str, *, provenance: FactSource, candidate_authorization: Mapping[str, object] | None = None) -> AuthorizationAssessment`
- `parse_compensation(sections: JobSections, *, provenance: FactSource) -> ParsedCompensation`
- `assess_compensation(*, structured_salary: SalaryRange | None, parsed: ParsedCompensation) -> CompensationAssessment`
- `assess_material_revision(job: Job, experience: ExperienceRequirement, authorization: AuthorizationAssessment, compensation: CompensationAssessment, candidate_state: Mapping[str, Any], *, policy: RevisionPolicy) -> MaterialRevision`
- `assess_freshness(job: Job, candidate_state: Mapping[str, Any], *, material_revision: MaterialRevision, now: datetime, freshness_days: int = 30) -> FreshnessAssessment`
- `extract_qualification_groups(sections: JobSections, profile: MatchProfile, *, provenance: FactSource) -> tuple[str, ...]`
- `match_qualification_groups(required_groups: tuple[str, ...], profile: MatchProfile) -> tuple[str, ...]`
- `assess_qualifications(required_groups: tuple[str, ...], matched_groups: tuple[str, ...], *, preferred_experience_above_three: bool, penalty_points: int = 2) -> QualificationAssessment`
- `score_job_v1(*, job: Job, stage_one: StageOneDecision, experience: ExperienceRequirement, authorization: AuthorizationAssessment, compensation: CompensationAssessment, freshness: FreshnessAssessment, qualification: QualificationAssessment, profile: MatchProfile, hard_blocks: tuple[str, ...] = ()) -> ScoringResult`
- `load_profile(path: str | Path) -> MatchProfile`
- `ResumeRouter(routes: tuple[ResumeRoute, ...]).select(decision: StageOneDecision) -> ResumeRecommendation | None`
- `evaluate_job(job: Job, candidate_id: str, candidate_state: Mapping[str, Any], profile: MatchProfile, now: datetime, *, revision_policy: RevisionPolicy, freshness_days: int = 30) -> JobAssessment`
