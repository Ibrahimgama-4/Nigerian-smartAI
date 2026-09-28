"""Days after planting is computed exactly. A growth STAGE is only returned when a sourced crop
calendar entry is supplied; we do not guess stages for crops/varieties without one."""
from datetime import date
from typing import Optional

def days_after_planting(planting: date, today: date) -> int:
    if planting > today:
        raise ValueError("planting date is in the future")
    return (today - planting).days

def stage_for(dap: int, calendar: Optional[list]) -> Optional[str]:
    """calendar: [{'stage': str, 'from_dap': int, 'to_dap': int, 'source': str}, ...]"""
    if not calendar:
        return None
    for s in calendar:
        if s["from_dap"] <= dap <= s["to_dap"]:
            return s["stage"]
    return None
