"""Canonical team definitions.

Team-based access control is enforced server-side in later phases; Phase 1
seeds the fixed set so users can be assigned to one or more teams.
"""

from __future__ import annotations

# Order is meaningful for display in the UI. The list position seeds each team's
# ``sort_order`` (see ``db.init_db``), so inserting a name here re-sequences the
# sidebar on the next start, even for existing databases.
DEFAULT_TEAMS: tuple[str, ...] = (
    "Detect and Response",
    "Threat Hunting",
    "Threat Intel",
    "Forensics & BID",
    "Advanced Analytics",
    "Red Team",
    "Threat Detection Engineering",
)
