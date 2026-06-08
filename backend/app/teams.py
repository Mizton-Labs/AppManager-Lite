"""Team helpers.

Teams are administrator-managed (created, renamed, reordered, and given an
optional icon from Settings). There is no fixed catalogue; a clean install
starts with no teams.

``slugify`` mirrors the frontend ``teamSlug`` so the backend can enforce that
two team names never collapse to the same URL slug.
"""

from __future__ import annotations

import re

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Build a URL-safe slug for a team name, e.g. ``Red Team`` -> ``red-team``.

    Mirrors the frontend ``teamSlug``: lowercase, runs of non-alphanumeric
    characters become a single dash, and leading/trailing dashes are trimmed.
    """
    return _SLUG_STRIP_RE.sub("-", name.lower()).strip("-")
