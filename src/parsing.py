"""Structure official job text without discarding its source context."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class JobSections:
    full_text: str
    required: tuple[str, ...]
    responsibilities: tuple[str, ...]
    preferred: tuple[str, ...]
    other: tuple[str, ...]


_HEADING_KIND = {
    "basic qualifications": "required",
    "minimum qualifications": "required",
    "required qualifications": "required",
    "qualifications": "required",
    "requirements": "required",
    "responsibilities": "responsibilities",
    "what you will do": "responsibilities",
    "preferred qualifications": "preferred",
    "nice to have": "preferred",
    "bonus": "preferred",
}
_HEADING_RE = re.compile(
    r"(?i)(?<![a-z])(" + "|".join(
        sorted((re.escape(value) for value in _HEADING_KIND), key=len, reverse=True)
    ) + r")(?:\s*:)?(?:\s+|$)"
)


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _sentences(value: str) -> tuple[str, ...]:
    normalised = _normalise(value)
    if not normalised:
        return ()
    return tuple(
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", normalised)
        if part.strip()
    )


def split_job_sections(text: str) -> JobSections:
    """Split normalized prose into the small set of matching contexts."""
    full_text = _normalise(text)
    buckets: dict[str, list[str]] = {
        "required": [],
        "responsibilities": [],
        "preferred": [],
        "other": [],
    }
    matches = list(_HEADING_RE.finditer(full_text))
    if not matches:
        buckets["other"].extend(_sentences(full_text))
    else:
        buckets["other"].extend(_sentences(full_text[: matches[0].start()]))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(full_text)
            kind = _HEADING_KIND[match.group(1).casefold()]
            buckets[kind].extend(_sentences(full_text[match.end() : end]))
    return JobSections(
        full_text=full_text,
        required=tuple(buckets["required"]),
        responsibilities=tuple(buckets["responsibilities"]),
        preferred=tuple(buckets["preferred"]),
        other=tuple(buckets["other"]),
    )
