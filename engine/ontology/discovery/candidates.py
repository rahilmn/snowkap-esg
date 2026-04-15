"""Discovery candidate dataclass and in-memory buffer.

Candidates are collected inline during article processing (~5ms) and
stored in a buffer that persists to JSON for crash recovery. The batch
promoter reads from this buffer periodically.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Discovery categories
# ---------------------------------------------------------------------------

CATEGORY_ENTITY = "entity"
CATEGORY_THEME = "theme"
CATEGORY_EVENT = "event"
CATEGORY_EDGE = "edge"
CATEGORY_WEIGHT = "weight"
CATEGORY_STAKEHOLDER = "stakeholder"
CATEGORY_FRAMEWORK = "framework"

# Status values
STATUS_PENDING = "pending"
STATUS_PROMOTED = "promoted"
STATUS_REJECTED = "rejected"
STATUS_ARCHIVED = "archived"


# ---------------------------------------------------------------------------
# DiscoveryCandidate
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryCandidate:
    """A single piece of knowledge discovered from article analysis."""

    category: str  # entity | theme | event | edge | weight | stakeholder | framework
    label: str  # human-readable label (e.g., "Tata Power", "Carbon Offset Fraud")
    slug: str  # URI-safe identifier (e.g., "tata_power", "carbon_offset_fraud")

    # Provenance
    article_ids: list[str] = field(default_factory=list)  # articles where discovered
    sources: list[str] = field(default_factory=list)  # news source names
    companies: list[str] = field(default_factory=list)  # company slugs
    confidence: float = 0.0  # 0.0-1.0
    first_seen: str = ""  # ISO timestamp
    last_seen: str = ""  # ISO timestamp

    # Category-specific data
    data: dict[str, Any] = field(default_factory=dict)
    # entity: {entity_type: "company|regulator|facility|supplier", industry: "...", domain: "..."}
    # theme: {pillar: "E|S|G", sub_metrics: [...]}
    # event: {score_floor: int, score_ceiling: int, keywords: [...], transmission: "..."}
    # edge: {source_primitive: "EP", target_primitive: "OX", direction: "+|-|mixed", beta: "0.1-0.3"}
    # weight: {topic: "...", industry: "...", current_weight: 0.5, observed_weight: 0.8}
    # stakeholder: {stakeholder_group: "Investors", concern: "...", transmission: "..."}
    # framework: {framework_id: "SEBI_2026_42", deadline: "2026-09-30", jurisdiction: "India"}

    # Status
    status: str = STATUS_PENDING  # pending | promoted | rejected | archived

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DiscoveryCandidate":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def merge(self, other: "DiscoveryCandidate") -> None:
        """Merge another observation of the same candidate (adds provenance)."""
        for aid in other.article_ids:
            if aid not in self.article_ids:
                self.article_ids.append(aid)
        for src in other.sources:
            if src not in self.sources:
                self.sources.append(src)
        for comp in other.companies:
            if comp not in self.companies:
                self.companies.append(comp)
        # Update confidence as running average
        n = len(self.article_ids)
        self.confidence = ((self.confidence * (n - 1)) + other.confidence) / n
        self.last_seen = other.last_seen or datetime.now(timezone.utc).isoformat()

    @property
    def article_count(self) -> int:
        return len(self.article_ids)

    @property
    def source_count(self) -> int:
        return len(set(self.sources))

    @property
    def company_count(self) -> int:
        return len(set(self.companies))


# ---------------------------------------------------------------------------
# DiscoveryBuffer — in-memory store with JSON persistence
# ---------------------------------------------------------------------------

_DEFAULT_STAGING_PATH = Path("data/ontology/discovery_staging.json")


class DiscoveryBuffer:
    """In-memory candidate buffer with JSON persistence for crash recovery."""

    def __init__(self, staging_path: Path | None = None) -> None:
        self._path = staging_path or _DEFAULT_STAGING_PATH
        self._candidates: dict[str, DiscoveryCandidate] = {}  # key = category:slug
        self._load()

    def _key(self, candidate: DiscoveryCandidate) -> str:
        return f"{candidate.category}:{candidate.slug}"

    def add(self, candidate: DiscoveryCandidate) -> None:
        """Add or merge a candidate into the buffer."""
        key = self._key(candidate)
        if key in self._candidates:
            self._candidates[key].merge(candidate)
        else:
            if not candidate.first_seen:
                candidate.first_seen = datetime.now(timezone.utc).isoformat()
            if not candidate.last_seen:
                candidate.last_seen = candidate.first_seen
            self._candidates[key] = candidate
        self._persist()

    def get_all(self, category: str | None = None, status: str | None = None) -> list[DiscoveryCandidate]:
        """Return candidates, optionally filtered by category and/or status."""
        result = list(self._candidates.values())
        if category:
            result = [c for c in result if c.category == category]
        if status:
            result = [c for c in result if c.status == status]
        return result

    def get(self, category: str, slug: str) -> DiscoveryCandidate | None:
        return self._candidates.get(f"{category}:{slug}")

    def update_status(self, category: str, slug: str, status: str) -> None:
        key = f"{category}:{slug}"
        if key in self._candidates:
            self._candidates[key].status = status
            self._persist()

    def remove(self, category: str, slug: str) -> None:
        key = f"{category}:{slug}"
        self._candidates.pop(key, None)
        self._persist()

    @property
    def count(self) -> int:
        return len(self._candidates)

    @property
    def pending_count(self) -> int:
        return sum(1 for c in self._candidates.values() if c.status == STATUS_PENDING)

    def _persist(self) -> None:
        """Write buffer to JSON for crash recovery."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "count": self.count,
                "candidates": {k: v.to_dict() for k, v in self._candidates.items()},
            }
            self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception as exc:
            logger.warning("DiscoveryBuffer persist failed: %s", exc)

    def _load(self) -> None:
        """Load buffer from JSON if it exists."""
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for key, cdict in data.get("candidates", {}).items():
                self._candidates[key] = DiscoveryCandidate.from_dict(cdict)
            logger.info("DiscoveryBuffer loaded %d candidates from %s", len(self._candidates), self._path)
        except Exception as exc:
            logger.warning("DiscoveryBuffer load failed: %s", exc)


# Module-level singleton
_buffer: DiscoveryBuffer | None = None


def get_buffer() -> DiscoveryBuffer:
    """Return the module-level singleton buffer."""
    global _buffer
    if _buffer is None:
        _buffer = DiscoveryBuffer()
    return _buffer
