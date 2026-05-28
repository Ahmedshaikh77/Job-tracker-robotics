"""Shared data models."""
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Job:
    """A single job posting."""
    company: str
    job_id: str  # unique within company
    title: str
    location: str
    url: str
    description: str = ""  # may be empty if fetcher doesn't return it
    posted_at: Optional[str] = None  # ISO string if available

    @property
    def key(self) -> str:
        """Unique key for dedupe across all companies."""
        return f"{self.company}:{self.job_id}"

    def to_dict(self) -> dict:
        return asdict(self)
