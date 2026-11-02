"""
Shared rules for what a structure timer means for the structure's registry state.

Used by manual / pasted timers (``views.timer_add``) and by timers created from
corptools notifications, so both keep the structure registry in the same state.
"""

# Standard Library
from typing import Any

# Django
from django.utils import timezone

# Structures with a single reinforcement cycle: the one timer is the final timer
SINGLE_REINFORCEMENT_KEYWORDS = (
    "mercenary den",
    "skyhook",
    "customs office",
    "ansiblex",
    "pharolux",
    "tenebrex",
)


def is_single_reinforcement_type(type_name: str | None) -> bool:
    name = (type_name or "").lower()
    return any(keyword in name for keyword in SINGLE_REINFORCEMENT_KEYWORDS)


def structure_state_for_timer(timer_type: str, type_name: str | None = "") -> str:
    """Registry state a structure is in while this timer is pending ('' = no change)."""
    timer_type = (timer_type or "").upper()
    if timer_type == "ARMOR":
        return "ARMOR"
    if timer_type == "HULL":
        return "REINFORCED" if is_single_reinforcement_type(type_name) else "HULL"
    if timer_type in ("ANCHORING", "UNANCHORING"):
        return timer_type
    return ""


def apply_timer_state(structure: Any, timer_type: str, timer_datetime: Any) -> bool:
    """
    Sets the structure's state from a pending timer. Past timers change nothing.
    Returns True when the structure was updated.
    """
    if not structure or not timer_datetime or timer_datetime <= timezone.now():
        return False
    type_name = getattr(getattr(structure, "structure_type", None), "name", "")
    state = structure_state_for_timer(timer_type, type_name)
    if not state or structure.state == state:
        return False
    structure.state = state
    structure.save(update_fields=["state", "updated_at"])
    return True
