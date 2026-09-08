"""Cheap, evidence-aware eligibility checks performed before detail fetching."""

from __future__ import annotations

from dataclasses import dataclass
import re
from enum import StrEnum

from .models import EmploymentType, FactSource, Job


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


TITLE_SIGNALS: dict[str, tuple[str, ...]] = {
    "camera_optical": (
        "camera",
        "optical",
        "imaging hardware",
        "opto-mechanical",
        "optomechanical",
    ),
    "mechanical_actuators": (
        "actuator",
        "actuation",
        "transmission",
        "drivetrain",
    ),
    "electronics_test": (
        "electronics test",
        "circuit board validation",
        "pcb validation",
        "board validation",
        "embedded hardware validation",
    ),
    "manufacturing_test": (
        "manufacturing test",
        "manufacturing validation",
        "production test",
        "automated test equipment",
        "ate engineer",
    ),
    "robotics_test_validation": (
        "robotics test",
        "robot validation",
        "hardware validation",
    ),
    "hardware_test": (
        "hardware test",
        "test engineer hardware",
        "reliability test",
        "electromechanical test",
        "mechanical test engineer",
    ),
    "junior_embedded": (
        "junior embedded",
        "embedded engineer i",
        "new grad firmware",
    ),
    "embedded_systems": ("embedded", "firmware"),
    "medical_robotics_software": (
        "medical robotics software",
        "surgical robotics software",
    ),
    "robotics_ml_systems": (
        "robotics ml",
        "robotics machine learning",
        "robot learning systems",
    ),
    "robotics_software": (
        "robotics software",
        "motion planning",
        "robot controls software",
        "ros 2",
        "ros2",
        "moveit 2",
        "moveit2",
    ),
    "robotics_systems_integration": (
        "robotics systems",
        "robotics integration",
        "systems integration",
        "sensor integration",
        "controls engineer",
        "automation engineer",
    ),
    "robotics_hardware_mechanical": (
        "robotics hardware",
        "mechatronics",
        "mechanical engineer robotics",
        "robotic product development",
        "robotics product development",
    ),
}


_TITLE_FAMILY_ORDER = tuple(TITLE_SIGNALS)
_SENIORITY = re.compile(
    r"\b(?:senior|sr|staff|principal|lead|manager|director|head|chief|fellow|executive|mid[ -]?level)\b|"
    r"\bengineer\s+(?:iii|iv|3|4)\b",
    re.IGNORECASE,
)
_STATE_ABBREVIATIONS = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
_STATE_NAMES = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
)
_FOREIGN_COUNTRIES = (
    "canada", "switzerland", "ireland", "united kingdom", "uk", "england",
    "scotland", "germany", "france", "spain", "italy", "netherlands",
    "belgium", "australia", "india", "china", "japan", "singapore",
    "mexico", "brazil", "poland", "romania", "sweden", "norway",
    "denmark", "finland", "austria", "portugal", "israel", "taiwan",
)


def _normalise_phrase(value: str) -> str:
    value = value.casefold().replace("&", " and ")
    value = re.sub(r"[-_/]+", " ", value)
    return re.sub(r"[^a-z0-9+]+", " ", value).strip()


def _contains_phrase(haystack: str, phrase: str) -> bool:
    normalised_phrase = _normalise_phrase(phrase)
    suffix = "s?" if " " not in normalised_phrase else ""
    return bool(re.search(rf"\b{re.escape(normalised_phrase)}{suffix}\b", haystack))


def classify_role_family(title: str) -> tuple[str | None, tuple[str, ...]]:
    """Return the first approved title family and the exact matching signals."""
    normalised = _normalise_phrase(title)

    manufacturing = tuple(
        signal
        for signal in TITLE_SIGNALS["manufacturing_test"]
        if _contains_phrase(normalised, signal)
    )
    if manufacturing:
        return "manufacturing_test", manufacturing

    validation = next(
        (
            signal
            for signal in ("system validation", "hardware validation")
            if _contains_phrase(normalised, signal)
        ),
        None,
    )
    product = next(
        (word for word in ("optimus", "humanoid") if _contains_phrase(normalised, word)),
        None,
    )
    if validation and product:
        return "optimus_validation", (validation,)

    for family in _TITLE_FAMILY_ORDER:
        if family == "manufacturing_test":
            continue
        matches = tuple(
            signal
            for signal in TITLE_SIGNALS[family]
            if _contains_phrase(normalised, signal)
        )
        if matches:
            return family, matches
    return None, ()


def _official_description(job: Job) -> str:
    if job.provenance.get("description", FactSource.UNAVAILABLE) is FactSource.OFFICIAL_DETAIL:
        return _normalise_phrase(job.description)
    return ""


def refine_role_family(
    job: Job,
    initial_family: str | None,
    matched_title_signals: tuple[str, ...],
) -> tuple[str | None, tuple[str, ...]]:
    """Apply the small set of approved contextual routing refinements."""
    if initial_family is None:
        return None, ()

    evidence = list(dict.fromkeys(matched_title_signals))
    title = _normalise_phrase(job.title)
    description = _official_description(job)
    context = f"{title} {description}".strip()
    company = _normalise_phrase(job.company)
    family = initial_family

    if initial_family == "optimus_validation":
        product = "optimus" if _contains_phrase(title, "optimus") else "humanoid"
        evidence.append(product)

    if initial_family == "hardware_test" and "mechanical test engineer" in matched_title_signals:
        humanoid_company = company in {"figure", "figure ai", "apptronik", "1x"}
        tesla_optimus = company == "tesla" and _contains_phrase(context, "optimus")
        explicit_humanoid = any(
            _contains_phrase(context, token) for token in ("humanoid", "optimus")
        )
        flight_company = company in {"zipline", "skydio"}
        explicit_flight = any(
            _contains_phrase(context, token) for token in ("flight", "drone", "aircraft")
        )
        if humanoid_company or tesla_optimus or explicit_humanoid:
            family = "humanoid_mechanical_test"
            if humanoid_company:
                evidence.append(f"company: {job.company}")
            else:
                evidence.append("humanoid context")
        elif flight_company or explicit_flight:
            family = "flight_mechanical_test"
            if flight_company:
                evidence.append(f"company: {job.company}")
            else:
                evidence.append("flight context")

    if initial_family == "robotics_systems_integration" and any(
        signal == "automation engineer" for signal in matched_title_signals
    ):
        if any(
            _contains_phrase(description, phrase)
            for phrase in (
                "factory",
                "production line",
                "manufacturing automation",
                "production automation",
            )
        ):
            family = "manufacturing_test"
            evidence.append("factory automation")

    return family, tuple(dict.fromkeys(evidence))


def _foreign_location_token(location: str) -> str | None:
    normalised = _normalise_phrase(location)
    for country in _FOREIGN_COUNTRIES:
        if _contains_phrase(normalised, country):
            return country
    return None


def is_us_location(job: Job) -> bool:
    """Accept only structured or explicit U.S. locations without contradictions."""
    location = (job.location or "").strip()
    code = (job.country_code or "").strip().upper()
    foreign = _foreign_location_token(location)
    if foreign:
        return False
    if code and code not in {"US", "USA"}:
        return False
    if code in {"US", "USA"}:
        return True
    if not location:
        return False

    normalised = _normalise_phrase(location)
    if re.search(r"\b(?:united states|usa|u s a|u s)\b", normalised):
        return True
    if normalised in {"remote", "worldwide remote", "remote worldwide", "global"}:
        return False
    if any(_contains_phrase(normalised, state) for state in _STATE_NAMES):
        return True
    comma_parts = [part.strip().upper() for part in location.split(",")]
    if any(part in _STATE_ABBREVIATIONS for part in comma_parts[1:]):
        return True
    region = (job.region or "").strip()
    if job.city and (
        region.upper() in _STATE_ABBREVIATIONS
        or any(_contains_phrase(_normalise_phrase(region), state) for state in _STATE_NAMES)
    ):
        return True
    return False


def resolve_employment(job: Job) -> tuple[EmploymentType, FactSource]:
    """Resolve employment only from authoritative structured or detail evidence."""
    source = job.provenance.get("employment_type", FactSource.UNAVAILABLE)
    if source in {FactSource.STRUCTURED_FEED, FactSource.OFFICIAL_DETAIL}:
        if job.employment_type is not EmploymentType.UNKNOWN:
            return job.employment_type, source

    description = _official_description(job)
    if not description:
        return EmploymentType.UNKNOWN, FactSource.UNAVAILABLE

    if re.search(
        r"\bnot (?:a )?full time (?:position|role|job)\b",
        description,
    ):
        return EmploymentType.OTHER, FactSource.OFFICIAL_DETAIL

    patterns = (
        (EmploymentType.PART_TIME, ("part time",)),
        (EmploymentType.CONTRACT, ("contract position", "contract role", "contractor")),
        (EmploymentType.TEMPORARY, ("temporary position", "seasonal position")),
        (EmploymentType.INTERNSHIP, ("internship", "intern position", "co op")),
        (
            EmploymentType.FULL_TIME,
            (
                "full time position",
                "full time role",
                "full time job",
                "position is full time",
                "role is full time",
                "employment type full time",
                "regular full time",
            ),
        ),
    )
    for employment_type, phrases in patterns:
        if any(_contains_phrase(description, phrase) for phrase in phrases):
            return employment_type, FactSource.OFFICIAL_DETAIL
    if description == "full time":
        return EmploymentType.FULL_TIME, FactSource.OFFICIAL_DETAIL
    return EmploymentType.UNKNOWN, FactSource.UNAVAILABLE


def stage_one(job: Job) -> StageOneDecision:
    """Classify a listing for rejection or official-detail enrichment."""
    location_ok = is_us_location(job)
    employment_type, employment_source = resolve_employment(job)
    family, signals = classify_role_family(job.title)
    refined_family, evidence = refine_role_family(job, family, signals)
    reasons: list[str] = []

    foreign = _foreign_location_token(job.location)
    if foreign and (job.country_code or "").strip().upper() in {"US", "USA"}:
        reasons.append("Conflicting United States country code and foreign location")
    elif not location_ok:
        reasons.append("Location is not confirmed as United States")
    if _SENIORITY.search(_normalise_phrase(job.title)):
        reasons.append("Title has a disallowed seniority signal")
    if refined_family is None:
        reasons.append("Title has no approved role-family signal")
    if employment_type not in {EmploymentType.FULL_TIME, EmploymentType.UNKNOWN}:
        reasons.append("Employment type is not full-time")

    status = StageOneStatus.REJECT if reasons else StageOneStatus.ENRICH
    confirmed = (
        employment_type is EmploymentType.FULL_TIME
        and employment_source in {FactSource.STRUCTURED_FEED, FactSource.OFFICIAL_DETAIL}
    )
    return StageOneDecision(
        status=status,
        role_family=refined_family,
        matched_title_signals=signals,
        role_evidence=evidence,
        us_location_confirmed=location_ok,
        employment_type=employment_type,
        employment_source=employment_source,
        full_time_confirmed=confirmed,
        reasons=tuple(reasons),
    )
