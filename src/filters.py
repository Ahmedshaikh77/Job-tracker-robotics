"""Filter logic for jobs. Score-based with include/exclude keyword lists."""
from __future__ import annotations
import re
from typing import Iterable
from .models import Job


class JobFilter:
    """Filters jobs based on title + location + (optional) description matching.

    Scoring:
      +3 per include_keyword hit (in title)
      +1 per include_keyword hit (in description)
      +2 per positive_level_signal hit (anywhere)
      -5 per exclude_keyword hit (in title)  -> usually disqualifies
      -1 per exclude_keyword hit (in description)
    Plus a hard location filter: location must contain at least one allowed_location token
    (or location is blank/unspecified, which we let pass).

    A job passes if final score >= min_score AND no hard-fail conditions.
    """

    def __init__(self, cfg: dict):
        f = cfg["filter"]
        self.include = [k.lower() for k in f.get("include_keywords", [])]
        self.exclude = [k.lower() for k in f.get("exclude_keywords", [])]
        # Terms that disqualify a job if found ANYWHERE (title or description).
        # Useful e.g. for international students filtering out clearance/citizenship-required roles.
        self.hard_exclude_anywhere = [k.lower() for k in f.get("hard_exclude_anywhere", [])]
        self.level_pos = [k.lower() for k in f.get("level_signals_positive", [])]
        self.locations = [k.lower() for k in f.get("locations", [])]
        self.min_score = f.get("min_score", 3)
        self.require_location_match = f.get("require_location_match", True)

    @staticmethod
    def _count_hits(text: str, needles: Iterable[str]) -> int:
        """Count distinct needles that appear in text. Each needle counts once."""
        return sum(1 for n in needles if n in text)

    def score(self, job: Job) -> tuple[int, dict]:
        """Return (score, breakdown) for a job."""
        title = (job.title or "").lower()
        desc = (job.description or "").lower()
        loc = (job.location or "").lower()
        combined = f"{title} {desc} {loc}"

        # Strong exclude check first (title-only) - these usually disqualify outright.
        # Use word-boundary check so "senior" doesn't match "seniority" etc.
        title_excludes = []
        for ex in self.exclude:
            # Match as whole word/phrase
            if re.search(rf"\b{re.escape(ex)}\b", title):
                title_excludes.append(ex)

        breakdown = {
            "include_in_title": [k for k in self.include if k in title],
            "include_in_desc": [k for k in self.include if k in desc and k not in (k for k in self.include if k in title)],
            "level_pos_hits": [k for k in self.level_pos if k in combined],
            "exclude_in_title": title_excludes,
            "exclude_in_desc": [k for k in self.exclude if k in desc and k not in title_excludes],
            "hard_exclude_hits": [k for k in self.hard_exclude_anywhere if k in combined],
            "location_ok": False,
            "location_text": loc,
        }

        score = 0
        score += 3 * len(breakdown["include_in_title"])
        score += 1 * len(breakdown["include_in_desc"])
        score += 2 * len(breakdown["level_pos_hits"])
        score -= 5 * len(breakdown["exclude_in_title"])
        score -= 1 * len(breakdown["exclude_in_desc"])

        # Location filter
        if loc:
            breakdown["location_ok"] = any(l in loc for l in self.locations)
        else:
            # No location info -> let it pass, since some fetchers don't return location
            breakdown["location_ok"] = True

        return score, breakdown

    def passes(self, job: Job) -> tuple[bool, int, dict]:
        score, bd = self.score(job)
        if bd["hard_exclude_hits"]:
            return False, score, bd
        if self.require_location_match and not bd["location_ok"]:
            return False, score, bd
        if bd["exclude_in_title"]:
            return False, score, bd
        if score < self.min_score:
            return False, score, bd
        return True, score, bd
