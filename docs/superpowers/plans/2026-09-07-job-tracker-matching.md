# Job Tracker Matching and Resume Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace substring filtering with evidence-aware eligibility, parsing, scoring, recency, and exact CV routing for Muhammad's target roles.

**Architecture:** A cheap Stage-1 gate selects role-shaped U.S. candidates for official detail enrichment. Small parsing modules then distinguish required from preferred experience, direct work-authorization language from general boilerplate, comparable base compensation, and authoritative freshness before a versioned scorer and ordered resume router produce the final assessment.

**Tech Stack:** Python 3.11, dataclasses, enum, decimal, zoneinfo, PyYAML, pytest

**Spec:** `docs/superpowers/specs/2026-09-07-job-tracker-alerts-v2-design.md`

## Global Constraints

- Only full-time U.S. roles can alert. Unknown employment type may proceed to detail enrichment but cannot alert while unresolved.
- Required zero to three years is eligible. Four years is flexible only under the approved explicit wording and requires a perfect 85-point non-experience subtotal; it is capped at `Moderate` and must flag the gap.
- `Apply Now` is 85 to 100, `Strong` is 75 to 84, `Moderate` is 55 to 74, and `Skip` is below 55 or hard-blocked.
- Confirmed salary, date, experience, employment, and authorization facts retain provenance. Fit, gaps, and CV selection are tracker assessments.
- Recent means an authoritative source date on or after the New York calendar date 30 days before evaluation. Unknown-date jobs qualify only when first discovered after seed.
- Non-USD compensation without a published USD equivalent is `Unresolved`, displays the original currency, and receives the same 3 points as unpublished compensation.
- Resume names are labels only. The tracker does not require the PDF files at runtime and never uploads them.
- Do not call live sources or send Telegram messages while implementing this phase.

**Prerequisite:** Complete the core-foundation and source-adapter plans first.

---

### Task 1: Replace the legacy filter with a typed Stage-1 gate

**Files:**
- Modify: `src/filters.py:1-92`
- Create: `src/eligibility.py`
- Create: `tests/test_stage_one.py`
- Create: `tests/test_legacy_filter_compat.py`

**Interfaces:**
- Consumes: normalized `Job` records and `FactSource`
- Produces: `StageOneStatus`, `StageOneDecision`, `classify_role_family(title) -> tuple[str | None, tuple[str, ...]]`, `refine_role_family(job, initial_family, matched_title_signals) -> tuple[str | None, tuple[str, ...]]`, `resolve_employment(job) -> tuple[EmploymentType, FactSource]`, `is_us_location(job) -> bool`, and `stage_one(job) -> StageOneDecision`

- [ ] **Step 1: Write failing role, seniority, location, and employment tests**

Create `tests/test_stage_one.py` with parameterized title cases and these regressions:

```python
@pytest.mark.parametrize(
    ("title", "expected_family"),
    [
        ("Robotics Systems Engineer", "robotics_systems_integration"),
        ("Robotics Test Engineer", "robotics_test_validation"),
        ("Hardware Validation Engineer I", "robotics_test_validation"),
        ("Hardware Test Engineer I", "hardware_test"),
        ("Electromechanical Test Engineer", "hardware_test"),
        ("Embedded Hardware Validation Engineer", "electronics_test"),
        ("Manufacturing Test Engineer, Robotics", "manufacturing_test"),
        ("Manufacturing Validation Engineer", "manufacturing_test"),
        ("Manufacturing Test Engineer, Optimus", "manufacturing_test"),
        ("System Validation Engineer, Optimus Hand", "optimus_validation"),
        ("System Validation Engineer", None),
        ("Software Engineer, Optimus", None),
        ("Automation Engineer", "robotics_systems_integration"),
        ("Manufacturing Process Engineer", None),
        ("Machine Learning Systems Engineer", None),
        ("Robotics ML Systems Engineer", "robotics_ml_systems"),
        ("ROS 2 Engineer", "robotics_software"),
        ("Embedded Software Engineer", "embedded_systems"),
        ("Mechanical Engineer, Actuators", "mechanical_actuators"),
        ("Mechanical Design Engineer", None),
        ("Senior Counsel", None),
    ],
)
def test_role_family_requires_a_title_signal(title, expected_family):
    family, _ = classify_role_family(title)
    assert family == expected_family


def test_lausanne_does_not_match_usa_substring(make_job):
    decision = stage_one(make_job(location="Lausanne, Switzerland", country_code=""))
    assert decision.status is StageOneStatus.REJECT
    assert "United States" in decision.reasons[0]


def test_conflicting_us_code_and_foreign_location_is_rejected(make_job):
    decision = stage_one(make_job(country_code="US", location="Toronto, Canada"))
    assert decision.status is StageOneStatus.REJECT
    assert "conflicting" in decision.reasons[0].lower()


@pytest.mark.parametrize("location", ["Remote", "Worldwide Remote", "Global"])
def test_unscoped_remote_is_withheld(make_job, location):
    assert stage_one(make_job(location=location, country_code="")).status is StageOneStatus.REJECT


def test_explicit_us_remote_is_enriched(make_job):
    job = make_job(
        location="Remote, United States",
        country_code="",
        employment_type=EmploymentType.FULL_TIME,
        provenance={"employment_type": FactSource.STRUCTURED_FEED},
    )
    assert stage_one(job).status is StageOneStatus.ENRICH


def test_unknown_employment_can_enrich_but_not_alert(make_job):
    decision = stage_one(make_job(employment_type=EmploymentType.UNKNOWN))
    assert decision.status is StageOneStatus.ENRICH
    assert decision.full_time_confirmed is False
```

Add rejection cases for intern, senior, sr., staff, principal, lead, manager, director, head, chief, fellow, and executive titles. Add Stage-1 enrichment cases for Engineer I, Engineer II, associate, entry-level, early-career, and titles with no level word but an allowed role family; the unresolved-experience final gate in Task 2 still decides whether an unlevelled role may alert.

- [ ] **Step 2: Run tests and verify the Lausanne and blank-employment failures**

```bash
python -m pytest tests/test_stage_one.py -v
```

Expected: the legacy substring filter passes Lausanne and has no tri-state result.

- [ ] **Step 3: Implement exact Stage-1 states and structured U.S. matching**

Use:

```python
class StageOneStatus(StrEnum):
    REJECT = "reject"
    ENRICH = "enrich"


@dataclass(frozen=True, slots=True)
class StageOneDecision:
    status: StageOneStatus
    role_family: str | None
    matched_title_signals: tuple[str, ...]
    role_evidence: tuple[str, ...]
    us_location_confirmed: bool
    employment_type: EmploymentType
    employment_source: FactSource
    full_time_confirmed: bool
    reasons: tuple[str, ...]
```

Reject contradictory evidence first, such as `country_code == "US"` paired with an explicit foreign country. Otherwise location precedence is: structured `job.country_code == "US"`; explicit `United States`, `USA`, or `U.S.` tokens; recognized city plus state name/abbreviation; explicit `Remote, United States`. The static region set is the 50 USPS state abbreviations plus `DC`, paired with full state names and District of Columbia, and matches only comma-separated or word-boundary location tokens. Reject blank, worldwide/global remote, and non-U.S. countries. Never search for the raw substring `usa`.

`resolve_employment()` applies a non-unknown normalized employment type first only when `job.provenance.get("employment_type", FactSource.UNAVAILABLE)` is `STRUCTURED_FEED` or `OFFICIAL_DETAIL`, then checks official-detail sentence evidence. Official-detail inference is allowed only when `job.provenance.get("description", FactSource.UNAVAILABLE) is FactSource.OFFICIAL_DETAIL`; it returns that exact provenance alongside the resolved enum. Reject explicit contract, temporary, part-time, seasonal, co-op, internship, and `EmploymentType.OTHER` values. Unknown or unprovenanced employment remains `ENRICH` but carries `EmploymentType.UNKNOWN`, `FactSource.UNAVAILABLE`, and `full_time_confirmed=False`. Add a test proving an unknown listing whose official detail says `This is a full-time position` resolves to full-time with `OFFICIAL_DETAIL` provenance, plus a control where identical untagged text remains unknown.

Use these ordered title-signal families, applying narrow signals before broad ones:

```python
TITLE_SIGNALS = {
    "camera_optical": ("camera", "optical", "imaging hardware", "opto-mechanical", "optomechanical"),
    "mechanical_actuators": ("actuator", "actuation", "transmission", "drivetrain"),
    "electronics_test": ("electronics test", "circuit board validation", "pcb validation", "board validation", "embedded hardware validation"),
    "manufacturing_test": ("manufacturing test", "manufacturing validation", "production test", "automated test equipment", "ate engineer"),
    "robotics_test_validation": ("robotics test", "robot validation", "hardware validation"),
    "hardware_test": ("hardware test", "test engineer hardware", "reliability test", "electromechanical test", "mechanical test engineer"),
    "junior_embedded": ("junior embedded", "embedded engineer i", "new grad firmware"),
    "embedded_systems": ("embedded", "firmware"),
    "medical_robotics_software": ("medical robotics software", "surgical robotics software"),
    "robotics_ml_systems": ("robotics ml", "robotics machine learning", "robot learning systems"),
    "robotics_software": ("robotics software", "motion planning", "robot controls software", "ros 2", "ros2", "moveit 2", "moveit2"),
    "robotics_systems_integration": ("robotics systems", "robotics integration", "systems integration", "sensor integration", "controls engineer", "automation engineer"),
    "robotics_hardware_mechanical": ("robotics hardware", "mechatronics", "mechanical engineer robotics", "robotic product development", "robotics product development"),
}
```

Before the general mapping, classify `optimus_validation` only when the title contains `Optimus` or `humanoid` together with `system validation` or `hardware validation`; bare `system validation` and a bare product word do not map. For that special case, return the matched validation phrase as the title signal and retain the product word as contextual evidence. Otherwise evaluate `TITLE_SIGNALS` family by family, stop at the first family with a match, and return every distinct normalized signal from that selected family that actually occurs in the title. Evaluate `manufacturing_test` before validation families, so `Manufacturing Test Engineer, Optimus` keeps the manufacturing-test route. `refine_role_family(job, initial_family, matched_title_signals)` is the sole owner of contextual overrides: it returns `(final_family, role_evidence)`, converts a broad mechanical-test result to `humanoid_mechanical_test` for Figure, Tesla Optimus, Apptronik, 1X, or explicit humanoid context; to `flight_mechanical_test` for Zipline, Skydio, or explicit flight/drone context; and converts factory-focused automation to `manufacturing_test`. It may use description context only when `job.provenance.get("description", FactSource.UNAVAILABLE)` is `OFFICIAL_DETAIL`. A generic manufacturing-process or machine-learning-systems title is not enough: the title must carry the manufacturing test/validation or robotics-plus-ML signal from the approved table. `stage_one()` stores the exact matched title phrases plus any company/product override in `role_evidence`. Neither refinement nor routing invents a family from description alone when the title lacks a target-family signal.

- [ ] **Step 4: Preserve a temporary legacy compatibility shim**

Move every useful case from `test_filter.py` into assertion-based tests and add the Lausanne regression. Keep `src.filters.JobFilter` as a thin compatibility wrapper around `stage_one()` until `src/main.py` and `src/verify.py` are cut over in the delivery plan; add an import-and-call smoke test in `tests/test_legacy_filter_compat.py`. Do not add new callers or retain the old keyword-score internals. The delivery orchestrator task removes the shim and root print-only script in the same commit as the caller cutover.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/test_stage_one.py tests/test_legacy_filter_compat.py -v
git add src/filters.py src/eligibility.py tests/test_stage_one.py tests/test_legacy_filter_compat.py
git commit -m "refactor: add structured stage one eligibility"
```

Expected: Stage-1 tests and the temporary compatibility smoke test pass.

---

### Task 2: Parse freshness, experience, authorization, and compensation

**Files:**
- Create: `src/parsing.py`
- Create: `src/freshness.py`
- Create: `src/experience.py`
- Create: `src/authorization.py`
- Create: `src/compensation.py`
- Create: `tests/test_freshness.py`
- Create: `tests/test_experience.py`
- Create: `tests/test_authorization.py`
- Create: `tests/test_compensation.py`

**Interfaces:**
- Consumes: `Job`, `SalaryRange`, `FactSource`, `ExperienceRequirement`, `AuthorizationAssessment`, shared `RevisionPolicy`, and the candidate record returned by `StateManager.observe_candidate()`
- Produces: typed `JobSections`, `ParsedCompensation`, `MaterialRevision`, `FreshnessAssessment`, and the exact parser/assessment functions defined in Step 5

- [ ] **Step 1: Write failing section and experience tests**

Test required and preferred context rather than global number matching:

```python
@pytest.mark.parametrize(
    ("text", "required_minimum", "blocked"),
    [
        ("Minimum qualifications: 0-2 years of experience.", 0, False),
        ("Minimum qualifications: 3+ years of experience.", 3, False),
        ("Minimum qualifications: 4+ years of experience.", 4, True),
        ("Preferred qualifications: 5+ years of experience.", None, False),
    ],
)
def test_required_and_preferred_years_are_distinct(text, required_minimum, blocked):
    result = parse_experience(
        split_job_sections(text),
        title="Robotics Test Engineer",
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    assert result.stated_required_minimum == required_minimum
    assert (result.stated_required_minimum is not None and result.stated_required_minimum > 3 and not result.flexible) is blocked


def test_parser_ignores_dates_voltages_and_product_versions():
    text = "Launched in 2026. Validate 24 V hardware running ROS 2."
    result = parse_experience(
        split_job_sections(text),
        title="Robotics Test Engineer",
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    assert result.stated_required_minimum is None


def test_degree_substitution_can_reduce_four_year_requirement():
    text = "Minimum: 4 years with a bachelor's degree, or 2 years with a master's degree."
    result = parse_experience(
        split_job_sections(text),
        title="Robotics Test Engineer",
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    assert result.stated_required_minimum == 4
    assert result.effective_required_minimum == 2
    assert result.flexible is True
```

Also test `typical`, `preferred`, and a range containing three as flexible; a hard five-year minimum remains blocked.

Add unresolved-experience cases. `Engineer I`, `Engineer II`, `Junior`, `Associate`, `Entry Level`, `Early Career`, or `New Graduate` in the title supports early-career scope. For an unlevelled title, require official responsibility text that describes individual execution within a team and contains no ownership of architecture, technical direction, roadmap, hiring, mentoring, or team leadership. Assert that an unlevelled role with absent numerical experience and ambiguous/leadership-heavy responsibilities is withheld, while a clearly team-scoped hands-on role is eligible but unresolved.

- [ ] **Step 2: Write failing authorization tests**

```python
@pytest.mark.parametrize(
    "text",
    [
        "Must be a U.S. citizen.",
        "Active Secret clearance required.",
        "This position is limited to U.S. persons under ITAR.",
        "We cannot sponsor now or in the future.",
    ],
)
def test_direct_personal_restrictions_are_blocked(text):
    result = classify_authorization(text, provenance=FactSource.OFFICIAL_DETAIL)
    assert result.status is AuthorizationStatus.BLOCKED


def test_general_export_boilerplate_is_not_a_personal_block():
    result = classify_authorization(
        "Products may be subject to U.S. export control laws.",
        provenance=FactSource.OFFICIAL_DETAIL,
    )
    assert result.status is AuthorizationStatus.UNKNOWN


def test_authorized_at_hire_supports_opt_inference_but_not_confirmed_sponsorship():
    result = classify_authorization(
        "Must be legally authorized to work in the United States at time of hire.",
        provenance=FactSource.OFFICIAL_DETAIL,
        candidate_authorization={
            "current_authorization": "F-1 OPT",
            "stem_opt_eligible": True,
            "future_sponsorship_needed": True,
        },
    )
    assert result.status is AuthorizationStatus.OPT_COMPATIBLE_UNCERTAIN
    assert result.source is FactSource.TRACKER_INFERENCE
```

Add `citizenship not required` as a non-blocking negation test. Add `test_structured_feed_cannot_confirm_or_block_authorization`, parameterized with sponsorship-offered, no-sponsorship, citizenship, clearance, U.S.-person, and ITAR phrases; every `FactSource.STRUCTURED_FEED` result remains `UNKNOWN`. Only `OFFICIAL_DETAIL` may establish confirmed support or a block. Inferred text never may.

- [ ] **Step 3: Write failing freshness and compensation tests**

Set `now` to `2026-09-07T20:00:00-04:00`. Assert `2026-08-08` is inside the inclusive 30-day New York-date window, `2026-08-07` is stale, unknown dates qualify only when first-seen after seed, and Greenhouse `updated_at` never substitutes for `posted_at`. Add a case where a role posted before the window has a newly observed official material eligibility change and becomes fresh for that revision, plus a control where a nonmaterial prose edit stays stale.

```python
@pytest.mark.parametrize(
    ("minimum", "maximum", "period", "currency", "label", "points"),
    [
        (100000, 140000, "year", "USD", "confirmed $100k+", 10),
        (80000, 120000, "year", "USD", "possible $100k+", 6),
        (70000, 90000, "year", "USD", "below target", 0),
        (50, 60, "hour", "USD", "confirmed $100k+", 10),
        (90000, 120000, "year", "EUR", "unresolved", 3),
    ],
)
def test_compensation_labels_and_points(minimum, maximum, period, currency, label, points):
    structured = SalaryRange(
        Decimal(minimum),
        Decimal(maximum),
        currency,
        PayPeriod(period),
        FactSource.STRUCTURED_FEED,
    )
    result = assess_compensation(
        structured_salary=structured,
        parsed=ParsedCompensation.not_published(),
    )
    assert (result.label, result.points) == (label, points)
```

Add cases proving bonuses, equity, overtime, and one-time awards are not added to base pay.

Add `parse_compensation(sections, *, provenance)` tests using official-detail salary sentences. Assert these distinct contracts:

- no structured salary and no base-pay statement produces `CompensationParseStatus.NOT_PUBLISHED`, then `CompensationStatus.UNPUBLISHED`, label `not published`, and 3 points;
- a base-pay statement with bounds but a missing currency, unsupported pay period, or otherwise ambiguous units produces `CompensationParseStatus.UNRESOLVED`, then `CompensationStatus.UNRESOLVED`, label `unresolved`, and 3 points;
- an explicit base-pay range with a currency and `hour` or `year` produces `PARSED` with a typed `SalaryRange`;
- a valid non-USD range without a published USD equivalent stays `UNRESOLVED`, preserves the original range and currency in evidence, and receives 3 points; and
- a structured adapter salary always takes precedence over prose, including over malformed prose.

Name the focused regressions `test_absent_base_pay_is_unpublished`, `test_malformed_base_pay_is_unresolved`, `test_structured_salary_precedes_malformed_prose`, and `test_non_usd_salary_preserves_original_evidence`. Add `test_untagged_posted_at_is_not_authoritative`, `test_material_revision_uses_last_alert_basis`, `test_cumulative_compensation_changes_use_last_alert_basis`, `test_nondefault_revision_policy_controls_compensation_materiality`, and `test_description_only_change_is_not_material`. The cumulative case applies two changes that are individually below `policy.compensation_material_change_ratio` but whose total from the last alerted basis reaches it. The nondefault case uses a 25 percent ratio and a $150,000 threshold: a 20 percent bound change that does not cross $150,000 is nonmaterial, while crossing $150,000 is material.

- [ ] **Step 4: Run parser tests and verify they fail**

```bash
python -m pytest tests/test_experience.py tests/test_authorization.py tests/test_freshness.py tests/test_compensation.py -v
```

Expected: imports fail because the parsers do not exist.

- [ ] **Step 5: Implement section-aware parsing and assessments**

Define the parser boundary before implementing its rules:

```python
class CompensationParseStatus(StrEnum):
    NOT_PUBLISHED = "not-published"
    PARSED = "parsed"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class JobSections:
    full_text: str
    required: tuple[str, ...]
    responsibilities: tuple[str, ...]
    preferred: tuple[str, ...]
    other: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedCompensation:
    status: CompensationParseStatus
    salary: SalaryRange | None
    evidence: str
    source: FactSource

    @classmethod
    def not_published(cls) -> "ParsedCompensation":
        return cls(
            CompensationParseStatus.NOT_PUBLISHED,
            None,
            "No base compensation published",
            FactSource.UNAVAILABLE,
        )


@dataclass(frozen=True, slots=True)
class MaterialRevision:
    changed_fields: tuple[str, ...]
    first_observed_at: str | None

    @property
    def is_material(self) -> bool:
        return bool(self.changed_fields)


def split_job_sections(text: str) -> JobSections: ...


def parse_experience(
    sections: JobSections,
    *,
    title: str,
    provenance: FactSource,
) -> ExperienceRequirement: ...


def classify_authorization(
    text: str,
    *,
    provenance: FactSource,
    candidate_authorization: Mapping[str, object] | None = None,
) -> AuthorizationAssessment: ...


def parse_compensation(
    sections: JobSections,
    *,
    provenance: FactSource,
) -> ParsedCompensation: ...


def assess_compensation(
    *,
    structured_salary: SalaryRange | None,
    parsed: ParsedCompensation,
) -> CompensationAssessment: ...


def assess_material_revision(
    job: Job,
    experience: ExperienceRequirement,
    authorization: AuthorizationAssessment,
    compensation: CompensationAssessment,
    candidate_state: Mapping[str, Any],
    *,
    policy: RevisionPolicy,
) -> MaterialRevision: ...


def assess_freshness(
    job: Job,
    candidate_state: Mapping[str, Any],
    *,
    material_revision: MaterialRevision,
    now: datetime,
) -> FreshnessAssessment: ...
```

`split_job_sections()` normalizes whitespace, retains normalized sentences in source order, and recognizes `minimum qualifications`, `required qualifications`, `requirements`, `responsibilities`, `what you will do`, `preferred qualifications`, `nice to have`, and `bonus` headings. Unheaded text belongs to `other`; `full_text` is the normalized entire posting. `parse_experience()` scans `required` first and `preferred` separately, accepts `0-3`, `0 to 3`, `1+`, `minimum of 2`, and singular/plural year forms, and retains the exact evidence sentence. It sets `unresolved=True` when no required numeric statement exists and computes `early_career_supported` from the explicit `title` plus responsibility rule above. Non-official provenance can retain unresolved evidence but cannot establish a decisive numeric requirement. The evaluator hard-blocks unresolved experience when `early_career_supported` is false; supported unresolved experience remains eligible and receives at most 10 of 15 points.

`classify_authorization()` checks negations before positive block phrases. Only `FactSource.OFFICIAL_DETAIL` may establish confirmed support or blocked. Direct sponsorship-offered language maps to confirmed support. Direct no-sponsorship-now-or-future, citizenship, clearance, U.S.-person, or personally applicable ITAR language maps to blocked. At-hire authorization language maps to the OPT-compatible tracker inference only when the explicit candidate record says F-1 OPT, STEM eligibility, and future sponsorship need; its source is `TRACKER_INFERENCE`. Missing candidate authorization, structured-feed-only, inferred, or general export language maps to unknown.

`parse_compensation()` accepts only `OFFICIAL_DETAIL` prose and never adds bonus, equity, overtime, or one-time pay. It returns `NOT_PUBLISHED` only when no base-pay claim exists; a base-pay claim whose currency or supported period cannot be determined returns `UNRESOLVED` with the original evidence and no fabricated range. `assess_compensation()` accepts a non-null `structured_salary` only when its `source` is `STRUCTURED_FEED` or `OFFICIAL_DETAIL`, prefers it over prose, otherwise uses the parsed official base range, annualizes hourly base at 2,080 hours, and implements the distinct unpublished/malformed/non-USD outcomes tested above. A non-null salary with `UNAVAILABLE` or `TRACKER_INFERENCE` provenance raises `ValueError` rather than becoming a confirmed employer fact.

`assess_material_revision()` imports both `RevisionPolicy` and `comparison_basis` from `src.lifecycle` and calls `comparison_basis(candidate_state)`. Compare title, employment type, parsed required-experience fields, authorization status, normalized location, and compensation against that single basis, not directly against `last_evaluation`. Emit only the canonical changed-field names `title`, `employment_type`, `required_experience`, `authorization`, `location`, and `compensation`, in that order. Compensation is material only when either comparable bound changes by at least `policy.compensation_material_change_ratio` or the annual range crosses `policy.compensation_threshold`. Do not define fallback ratio or threshold constants in matching or `profile.yaml`; configuration constructs the shared policy. A description/content-hash-only edit is not material. Use the candidate record's current `last_seen_at` as `first_observed_at` only after a material difference is established. `assess_freshness()` treats `posted_at` as authoritative only when `job.provenance.get("posted_at", FactSource.UNAVAILABLE)` is `STRUCTURED_FEED` or `OFFICIAL_DETAIL` and compares New York calendar dates inclusively; `updated_at` never substitutes for it. A stale authoritative posting becomes fresh only for such a material revision. The displayed posted date remains the original authoritative date. If no comparison basis exists, normal posted-date or post-seed first-discovery rules apply rather than manufacturing a revision.

- [ ] **Step 6: Run parser tests and commit**

```bash
python -m pytest tests/test_experience.py tests/test_authorization.py tests/test_freshness.py tests/test_compensation.py -v
git add src/parsing.py src/experience.py src/authorization.py src/freshness.py src/compensation.py tests/test_experience.py tests/test_authorization.py tests/test_freshness.py tests/test_compensation.py
git commit -m "feat: parse job eligibility evidence"
```

Expected: all parser tests pass.

---

### Task 3: Implement versioned 100-point scoring

**Files:**
- Create: `profile.yaml`
- Create: `src/profile.py`
- Create: `src/qualifications.py`
- Create: `src/scoring.py`
- Create: `tests/test_profile.py`
- Create: `tests/test_qualifications.py`
- Create: `tests/test_scoring.py`

**Interfaces:**
- Consumes: Stage-1 decision, parsed experience/authorization/compensation/freshness, normalized job, and validated `profile.yaml`
- Produces: immutable `MatchProfile`, `load_profile()`, `extract_qualification_groups()`, `match_qualification_groups()`, `QualificationAssessment`, `ScoringResult`, `score_job_v1()`, and exact recommendation boundaries

- [ ] **Step 1: Define the candidate evidence inventory and scoring configuration**

Create `profile.yaml` with the user's stated evidence only:

```yaml
profile_version: 1
candidate:
  degree: MS Mechanical Engineering and Materials Science, Duke University
  current_authorization: F-1 OPT
  stem_opt_eligible: true
  future_sponsorship_needed: true
skills:
  programming: [Python, C, C++, MATLAB]
  robotics: [ROS 2, MoveIt 2]
  platforms: [Linux, Git, ESP32, Jetson]
  cad: [SolidWorks, Fusion 360]
  sensing: [RealSense, IMU, load cell, encoder, camera]
  actuation: [motor, actuator]
domains:
  - robotics systems
  - electromechanical systems
  - embedded systems
  - mechanical design
  - sensor integration
  - test automation
  - hardware validation
  - system integration
  - failure analysis
skill_aliases:
  Python: [python]
  C: [c programming, embedded c]
  C++: [c++, cpp]
  MATLAB: [matlab]
  ROS 2: [ros 2, ros2]
  MoveIt 2: [moveit 2, moveit2]
  Linux: [linux]
  Git: [git]
  SolidWorks: [solidworks]
  Fusion 360: [fusion 360]
  ESP32: [esp32]
  Jetson: [jetson]
  RealSense: [realsense]
  IMU: [imu, inertial measurement unit]
  load cell: [load cell]
  encoder: [encoder]
  camera: [camera, imaging sensor]
  motor: [motor]
  actuator: [actuator]
scoring:
  version: 1
  preferred_experience_gap_penalty: 2
  caps:
    role_alignment: 25
    technical_evidence: 15
    experience_fit: 15
    domain_alignment: 10
    qualification_coverage: 10
    authorization: 10
    compensation: 10
    recency: 5
resume_routes:
  - {family: camera_optical, filename: "Muhammad Ahmed Mechanical Engineer Camera Optical Zipline.pdf"}
  - {family: mechanical_actuators, filename: "Muhammad Ahmed Mechanical Engineer Actuators.pdf"}
  - {family: medical_robotics_software, filename: "Muhammad Ahmed Software Engineer Medical Robotics.pdf"}
  - {family: robotics_ml_systems, filename: "Muhammad Ahmed Robotics ML Systems Engineer.pdf"}
  - {family: junior_embedded, filename: "Muhammad Ahmed Junior Embedded Engineer.pdf"}
  - {family: embedded_systems, filename: "Embedded_Software_Engineer.pdf"}
  - {family: electronics_test, filename: "Muhammad Ahmed Electronics Test Engineer.pdf"}
  - {family: optimus_validation, filename: "Mechatronics Engineer, Optimus Hardware Validation .pdf"}
  - {family: robotics_test_validation, filename: "Mechatronics Engineer, Optimus Hardware Validation .pdf"}
  - {family: humanoid_mechanical_test, filename: "Muhammad Ahmed Mechanical Test Engineer Figure.pdf"}
  - {family: flight_mechanical_test, filename: "Muhammad Ahmed Mechanical Test Engineer Zipline.pdf"}
  - {family: manufacturing_test, filename: "Muhammad Ahmed Manufacturing Test Engineer Tesla Optimus.pdf"}
  - {family: hardware_test, filename: "Muhammad Ahmed Hardware Test Engineer.pdf"}
  - {family: robotics_software, filename: "Muhammad Ahmed Robotics Software Engineer Fluidstack.pdf"}
  - {family: robotics_systems_integration, filename: "Muhammad Ahmed Robotics Sensing Integration Engineer.pdf"}
  - {family: robotics_hardware_mechanical, filename: "Muhammad Ahmed Mechanical Engineer Robotics Hardware.pdf"}
```

Define the typed configuration boundary in `src/profile.py`:

```python
class ProfileValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    degree: str
    current_authorization: str
    stem_opt_eligible: bool
    future_sponsorship_needed: bool


@dataclass(frozen=True, slots=True)
class ScoringCaps:
    role_alignment: int
    technical_evidence: int
    experience_fit: int
    domain_alignment: int
    qualification_coverage: int
    authorization: int
    compensation: int
    recency: int


@dataclass(frozen=True, slots=True)
class ScoringPolicy:
    version: int
    preferred_experience_gap_penalty: int
    caps: ScoringCaps


@dataclass(frozen=True, slots=True)
class ResumeRoute:
    family: str
    filename: str


@dataclass(frozen=True, slots=True)
class MatchProfile:
    profile_version: int
    candidate: CandidateEvidence
    skills: Mapping[str, tuple[str, ...]]
    domains: tuple[str, ...]
    skill_aliases: Mapping[str, tuple[str, ...]]
    scoring: ScoringPolicy
    resume_routes: tuple[ResumeRoute, ...]


def load_profile(path: str | Path) -> MatchProfile: ...
```

`load_profile()` uses `yaml.safe_load()`, rejects non-mappings, unknown or missing keys at every level, wrong scalar types, empty/duplicate normalized aliases, a skill without a canonical alias entry, an alias key not present in `skills`, unsupported profile/scoring versions, duplicate route families, and any missing or extra resume family. Require the exact caps `25/15/15/10/10/10/10/5`, totaling 100, and the exact ordered family/filename pairs shown above. Convert every sequence to a tuple and every nested mapping to a read-only mapping before returning; callers never receive the mutable YAML objects.

- [ ] **Step 2: Write failing profile, component, and boundary tests**

In `tests/test_profile.py`, assert the checked-in file loads as `MatchProfile`, the exact scoring caps total 100, the candidate authorization booleans retain boolean types, and all 16 ordered routes preserve their exact filenames (including the space before `.pdf` in the Optimus filename). Parameterize malformed copies for an unknown key, string boolean, duplicate alias, missing family, extra family, duplicate route family, wrong filename, wrong order, and unsupported profile or scoring version; each must raise `ProfileValidationError` rather than silently defaulting.

In `tests/test_scoring.py`, assert every cap and these exact rules:

```python
@pytest.mark.parametrize(
    ("required_minimum", "unresolved", "points"),
    [(0, False, 15), (1, False, 15), (2, False, 14), (3, False, 12), (None, True, 10)],
)
def test_experience_points(required_minimum, unresolved, points):
    assert experience_points(required_minimum, unresolved=unresolved) == points


@pytest.mark.parametrize(
    ("score", "recommendation"),
    [(100, "Apply Now"), (85, "Apply Now"), (84, "Strong"), (75, "Strong"), (74, "Moderate"), (55, "Moderate"), (54, "Skip")],
)
def test_recommendation_boundaries(score, recommendation):
    assert recommendation_for(score).value == recommendation
```

Role alignment consumes only the `StageOneDecision.matched_title_signals` carried through contextual refinement; it never re-scans description or requires the contextual final family to appear in `TITLE_SIGNALS`. Award 25 when at least one matched signal contains two or more tokens, 20 when at least two distinct one-token signals match, 15 for exactly one one-token signal, and 0 otherwise; phrase matching uses normalized token boundaries. Technical evidence grants 3 points per distinct matching skill group, capped at 15. Match only the explicit `skill_aliases` above with token or phrase boundaries; in particular, the single letter `C` is never a raw substring match. Domain alignment grants 5 points per matching domain, capped at 10. Qualification coverage is `round(10 * matched_required_groups / required_groups)` and defaults to 5 when no structured required group can be extracted. Authorization and compensation use the approved `10/7/4/blocked` and `10/6/3/0` rules. Recency is 5 for 0-7 days, 3 for 8-14 days, 1 for 15-30 days, and 2 for a qualifying unknown-date post-seed discovery. Technical and domain evidence may scan the title plus description only when the description provenance is `OFFICIAL_DETAIL`; untagged or structured-feed-only description text cannot add evidence.

Add tests that `C` does not match words such as `camera` or `mechanical`, `C++` does match, `ROS2` maps to `ROS 2`, one repeated skill counts once, and repeated role phrases cannot exceed their component cap.

Add tests that the component sum never exceeds 100, a hard block always returns Skip, and a flexible four-year role needs an 85 non-experience subtotal, becomes Moderate, and names the experience gap.

In `tests/test_qualifications.py`, prove `extract_qualification_groups(sections, profile, provenance=FactSource.OFFICIAL_DETAIL)` extracts distinct canonical `skill:`, `domain:`, and `degree:` groups only from required/minimum sections, excluding experience and authorization sentences. It returns an empty tuple when no structured group exists or provenance is not `OFFICIAL_DETAIL`. Prove `match_qualification_groups(required_groups, profile)` matches only explicit candidate evidence, deduplicates repeated requirements, and returns matched canonical groups in required-group order. The stated MS in Mechanical Engineering and Materials Science satisfies an explicitly required BS, BA, or MS in Mechanical Engineering, Materials Science, or a posting phrase that names those as acceptable alternatives; it does not satisfy Electrical Engineering, Computer Science, or an unspecified unrelated degree. Missing evidence remains missing and is never inferred. `assess_qualifications()` calculates `round(10 * matched / required)`, defaults to 5 when there are no required groups, and subtracts exactly 2 points, floored at zero, when the official posting has preferred experience above three years. The preferred-experience penalty is a gap, never a hard block.

Add the degree-equivalency end-to-end scoring regression. The parsed record must retain `stated_required_minimum=4`, `effective_required_minimum=2`, `flexible=True`, and the exact education-substitution basis. Because the stated minimum exceeds three, it still uses the flexible-four-year exception: require an 85-point subtotal excluding experience, cap the final recommendation at `Moderate`, and flag the stated four-year gap even though the effective minimum is two.

- [ ] **Step 3: Run scoring tests and verify failure**

```bash
python -m pytest tests/test_profile.py tests/test_qualifications.py tests/test_scoring.py -v
```

Expected: the typed profile loader, qualification matcher, and scorer are absent.

- [ ] **Step 4: Implement explicit scoring outputs**

Create exact public signatures:

```python
def extract_qualification_groups(
    sections: JobSections,
    profile: MatchProfile,
    *,
    provenance: FactSource,
) -> tuple[str, ...]: ...


def match_qualification_groups(
    required_groups: tuple[str, ...],
    profile: MatchProfile,
) -> tuple[str, ...]: ...


def assess_qualifications(
    required_groups: tuple[str, ...],
    matched_groups: tuple[str, ...],
    *,
    preferred_experience_above_three: bool,
    penalty_points: int = 2,
) -> QualificationAssessment: ...


def score_job_v1(
    *,
    job: Job,
    stage_one: StageOneDecision,
    experience: ExperienceRequirement,
    authorization: AuthorizationAssessment,
    compensation: CompensationAssessment,
    freshness: FreshnessAssessment,
    qualification: QualificationAssessment,
    profile: MatchProfile,
    hard_blocks: tuple[str, ...] = (),
) -> ScoringResult: ...
```

Use the shared `ScoreBreakdown` and `ScoringResult` dataclasses from `src.models`; do not redefine them. Validate every component against `profile.scoring.caps` and derive `score == breakdown.total`. `score_job_v1()` returns the breakdown, recommendation, top three matched evidence groups as the match reason, and the highest-priority missing required group as the gap. Never claim a skill absent from the validated profile.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/test_profile.py tests/test_qualifications.py tests/test_scoring.py -v
git add profile.yaml src/profile.py src/qualifications.py src/scoring.py tests/test_profile.py tests/test_qualifications.py tests/test_scoring.py
git commit -m "feat: add versioned job scoring"
```

Expected: all scoring tests pass.

---

### Task 4: Add ordered role-to-resume routing

**Files:**
- Create: `src/resumes.py`
- Create: `tests/test_resume_routing.py`

**Interfaces:**
- Consumes: a `StageOneDecision` plus the validated `tuple[ResumeRoute, ...]` loaded in Task 3
- Produces: `ResumeRecommendation` and `ResumeRouter.select(decision)`

- [ ] **Step 1: Assert the exact approved route inventory**

Use the already-loaded and validated route tuple from Task 3. The loader is the owner of exact family/filename inventory; this task must not reload YAML or duplicate a second fallback mapping. Factory automation is refined to `manufacturing_test`, sensing/control integration to `robotics_systems_integration`, and robotic mechanical product development to `robotics_hardware_mechanical` by `refine_role_family()` in Task 1 before routing.

- [ ] **Step 2: Write failing route and precedence tests**

Parameterize every row of the approved role-to-resume table, including multiple title signals that share one route, and assert the exact filename and role-family evidence for each row. Do not reduce coverage to one test per unique CV. Add precedence tests for electronics over generic hardware test, integrated robotics/hardware validation over general hardware test, actuator/optical over generic mechanical, junior over generic embedded, humanoid versus flight mechanical test, and factory automation versus system controls. Assert an unmapped role returns `None` and records the evidence used when a route is selected.

```python
def test_unmapped_role_has_no_generic_fallback(router, make_job):
    decision = stage_one(make_job(title="Facilities Planner"))
    assert router.select(decision) is None


def test_optimus_validation_preserves_exact_filename(router, make_job):
    decision = stage_one(make_job(title="System Validation Engineer, Optimus Hand"))
    result = router.select(decision)
    assert result.filename == "Mechatronics Engineer, Optimus Hardware Validation .pdf"
    assert result.evidence == decision.role_evidence
    assert result.reason == "; ".join(decision.role_evidence)
```

- [ ] **Step 3: Run route tests and verify failure**

```bash
python -m pytest tests/test_resume_routing.py -v
```

Expected: resume router is absent.

- [ ] **Step 4: Implement ordered routing without filesystem access**

```python
@dataclass(frozen=True, slots=True)
class ResumeRecommendation:
    filename: str
    role_family: str
    evidence: tuple[str, ...]

    @property
    def reason(self) -> str:
        return "; ".join(self.evidence)


class ResumeRouter:
    def __init__(self, routes: tuple[ResumeRoute, ...]):
        self.routes = routes

    def select(self, decision: StageOneDecision) -> ResumeRecommendation | None:
        if decision.status is StageOneStatus.REJECT or decision.role_family is None:
            return None
        for route in self.routes:
            if route.family == decision.role_family:
                if not decision.role_evidence:
                    raise ValueError("mapped role requires exact Stage-1 evidence")
                return ResumeRecommendation(
                    route.filename,
                    decision.role_family,
                    decision.role_evidence,
                )
        return None
```

`refine_role_family()` remains the sole owner of company, product, factory, flight, humanoid, and other contextual decisions. The router never examines `Job`, title, description, or company, never creates generic evidence such as `title mapped to ...`, and never checks whether Desktop files exist. Its returned evidence is exactly `StageOneDecision.role_evidence`.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/test_resume_routing.py -v
git add src/resumes.py tests/test_resume_routing.py
git commit -m "feat: route roles to tailored resumes"
```

Expected: all routes and precedence cases pass.

---

### Task 5: Build the final evaluation boundary

**Files:**
- Create: `src/evaluation.py`
- Create: `tests/test_evaluation.py`

**Interfaces:**
- Consumes: enriched `Job`, the candidate snapshot returned by `StateManager.observe_candidate()`, typed `MatchProfile`, loaded `RevisionPolicy`, parsers, scorer, and resume router
- Produces: `evaluate_job(job, candidate_id, candidate_state, profile, now, *, revision_policy) -> JobAssessment`

- [ ] **Step 1: Write failing end-to-end assessment tests**

Create complete cases for an Apply Now role, a Moderate unknown-sponsorship role, a blocked ITAR role, an old role, unresolved full-time status, supported and unsupported unresolved experience, a hard four-year requirement, a valid flexible-four-year exception, and an unmapped role.

```python
def observe_for_evaluation(tmp_path, job):
    state = StateManager.load(tmp_path / "state.json")
    candidate_id, _, candidate = state.observe_candidate(
        job,
        NOW,
        discovered_during_seed=False,
    )
    return candidate_id, candidate


def test_assessment_distinguishes_confirmed_facts_from_tracker_inference(
    tmp_path, profile, make_job
):
    job = make_job(
        employment_type=EmploymentType.FULL_TIME,
        posted_at="2026-09-05",
        description="Minimum qualifications: 2 years. Must be authorized at hire. Python and hardware validation.",
        provenance={
            "description": FactSource.OFFICIAL_DETAIL,
            "posted_at": FactSource.OFFICIAL_DETAIL,
            "employment_type": FactSource.STRUCTURED_FEED,
        },
    )
    candidate_id, candidate = observe_for_evaluation(tmp_path, job)
    result = evaluate_job(
        job,
        candidate_id,
        candidate,
        profile,
        NOW,
        revision_policy=RevisionPolicy(),
    )
    assert result.candidate_id == candidate_id
    assert result.reopen_generation == candidate["reopen_generation"]
    assert result.experience.source is FactSource.OFFICIAL_DETAIL
    assert result.authorization.source is FactSource.TRACKER_INFERENCE
    assert result.resume_filename == "Mechatronics Engineer, Optimus Hardware Validation .pdf"


def test_unresolved_full_time_or_resume_mapping_is_withheld(
    tmp_path, profile, make_job
):
    job = make_job(employment_type=EmploymentType.UNKNOWN, title="Facilities Planner")
    candidate_id, candidate = observe_for_evaluation(tmp_path, job)
    result = evaluate_job(
        job,
        candidate_id,
        candidate,
        profile,
        NOW,
        revision_policy=RevisionPolicy(),
    )
    assert result.eligible is False
    assert result.recommendation is Recommendation.SKIP
```

Use `StateManager.observe_candidate()` for every end-to-end fixture rather than hand-writing candidate dictionaries or candidate IDs. Assert the returned `JobAssessment` preserves the observed `candidate_id`, current `reopen_generation`, complete `ScoreBreakdown`, `FreshnessAssessment`, `CompensationAssessment`, `QualificationAssessment`, and exact `resume_reason`; `score` must equal `score_breakdown.total`. Add a close/reopen fixture that observes the same candidate through the lifecycle API and proves the assessment uses the incremented generation. Add a Cobot fixture whose official detail says the applicant must be a U.S. person and prove the end-to-end result is blocked and never alertable. Add provenance regressions proving a missing `description` key and structured-feed-only restriction text both remain safe and cannot produce a confirmed authorization block. Add `test_evaluate_job_composes_nondefault_revision_policy`: create the comparison basis through state APIs, pass `RevisionPolicy(compensation_material_change_ratio=Decimal("0.25"), compensation_threshold=Decimal("150000"))`, and prove the same nonmaterial-versus-threshold-crossing outcomes as the focused freshness test. This test fails if `evaluate_job()` drops the caller's policy or substitutes defaults.

- [ ] **Step 2: Run evaluation tests and verify failure**

```bash
python -m pytest tests/test_evaluation.py -v
```

Expected: the evaluation coordinator is absent.

- [ ] **Step 3: Implement the final gate in one deterministic order**

Use this public signature:

```python
def evaluate_job(
    job: Job,
    candidate_id: str,
    candidate_state: Mapping[str, Any],
    profile: MatchProfile,
    now: datetime,
    *,
    revision_policy: RevisionPolicy,
) -> JobAssessment: ...
```

`evaluate_job()` receives the candidate ID and candidate snapshot resolved by `StateManager.observe_candidate()`; it must not independently resolve or change identity. The composition root passes the same `RevisionPolicy` instance built from `settings.matching_policy` to both `StateManager.load(..., revision_policy=revision_policy)` and every `evaluate_job(..., revision_policy=revision_policy)` call. Execute these steps in order:

1. Run `stage_one(job)` again on the enriched record so official-detail employment and role context are reflected. If `resolve_employment()` supplied an authoritative type not already normalized on `job`, create `evaluated_job = replace(job, employment_type=stage_one_decision.employment_type, provenance={**dict(job.provenance), "employment_type": stage_one_decision.employment_source})`; otherwise `evaluated_job = job`. This is a new frozen value, not an in-place mutation.
2. Read `description_source = job.provenance.get("description", FactSource.UNAVAILABLE)`; never index provenance directly. Split the description and call `parse_experience(sections, title=job.title, provenance=description_source)`.
3. Call `classify_authorization(sections.full_text, provenance=description_source, candidate_authorization={"current_authorization": profile.candidate.current_authorization, "stem_opt_eligible": profile.candidate.stem_opt_eligible, "future_sponsorship_needed": profile.candidate.future_sponsorship_needed})`.
4. Call `parse_compensation(sections, provenance=description_source)`, then `assess_compensation(structured_salary=job.salary, parsed=parsed_compensation)`.
5. Call `extract_qualification_groups(sections, profile, provenance=description_source)`, `match_qualification_groups(required_groups, profile)`, and `assess_qualifications(..., penalty_points=profile.scoring.preferred_experience_gap_penalty)`.
6. Call `assess_material_revision(evaluated_job, ..., policy=revision_policy)`; it and the later lifecycle decision both use `comparison_basis(candidate_state)`. Pass that typed result to `assess_freshness(evaluated_job, ...)`. Never construct or default a second `RevisionPolicy` inside the evaluator.
7. Apply the U.S., confirmed full-time, authorization, experience, freshness, and other hard gates; compute scoring version 1 against `evaluated_job`; then call `ResumeRouter(profile.resume_routes).select(stage_one_decision)`.
8. Withhold an otherwise passing role when no exact resume maps, and build `JobAssessment(job=evaluated_job, ...)` with `candidate_state["reopen_generation"]` unchanged. This keeps the core `JobAssessment` full-time invariant consistent with the authoritative Stage-1 fact.

The match reason lists only verified profile overlap. The gap prioritizes missing required qualifications, experience flexibility, sponsorship uncertainty, then missing or unresolved salary. `resume_reason` is `resume.reason`, which is the exact semicolon-joined `StageOneDecision.role_evidence`; the evaluator and router do not repeat contextual routing logic.

```python
if hard_blocks or resume is None:
    return replace(
        assessment,
        eligible=False,
        recommendation=Recommendation.SKIP,
        hard_blocks=tuple(hard_blocks),
    )
```

- [ ] **Step 4: Run all matching tests**

```bash
python -m pytest tests/test_stage_one.py tests/test_legacy_filter_compat.py tests/test_experience.py tests/test_authorization.py tests/test_freshness.py tests/test_compensation.py tests/test_profile.py tests/test_qualifications.py tests/test_scoring.py tests/test_resume_routing.py tests/test_evaluation.py -v
```

Expected: all matching and routing tests pass.

- [ ] **Step 5: Commit the evaluation boundary**

```bash
git add src/evaluation.py tests/test_evaluation.py
git commit -m "feat: evaluate verified job matches"
```

## Matching Verification Gate

Run:

```bash
python -m pytest -q
python -m compileall -q src tests
python -c 'from src.evaluation import evaluate_job; from src.filters import JobFilter; from src.profile import load_profile'
git status --short
```

Expected: the full suite passes and the worktree is clean before delivery integration.
