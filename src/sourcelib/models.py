"""What a crawl produces.

These live here rather than in the application because the interpreter cannot reach into it,
and because more than one consumer will read them. They are shape-compatible with the
crawler's own models so the adapter at the tier boundary is a constructor call rather than a
translation.

One difference is deliberate. ``authors`` is a list, because that is what ``all: true``
naturally yields and what a site with two authors actually has. The crawler joins it into one
comma-separated string today, so the adapter joins; when the crawler takes the list the
difference disappears.

Extras matter as much as the declared fields. An ItemList may carry keys beyond those a stage
names, and a chapter reaches its own URL through one, so anything extra is preserved rather
than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["Chapter", "Novel", "SearchResult", "Volume"]


@dataclass
class SearchResult:
    """One row of a search."""

    title: str
    url: str
    info: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Chapter:
    """One chapter, before and after its body is fetched."""

    id: int
    url: str = ""
    title: str = ""
    volume: Optional[int] = None
    body: Optional[str] = None
    images: Dict[str, str] = field(default_factory=dict)
    success: bool = False
    #: Keys the table of contents carried beyond the named fields, readable as
    #: ``{chapter.<key>}`` when building this chapter's own request.
    extras: Dict[str, Any] = field(default_factory=dict)

    def context(self) -> Dict[str, Any]:
        """What ``{chapter.*}`` resolves against."""
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "volume": self.volume,
            **self.extras,
        }


@dataclass
class Volume:
    """A group of chapters, whether the site declared it or it was derived."""

    id: int
    title: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Novel:
    """A novel and its table of contents."""

    url: str
    title: str = ""
    cover_url: str = ""
    authors: List[str] = field(default_factory=list)
    synopsis: str = ""
    tags: List[str] = field(default_factory=list)
    language: Optional[str] = None
    is_manga: Optional[bool] = None
    is_mtl: Optional[bool] = None
    volumes: List[Volume] = field(default_factory=list)
    chapters: List[Chapter] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def author(self) -> str:
        """The authors as the crawler stores them today, so the adapter need not join."""
        return ", ".join(a for a in self.authors if a)
