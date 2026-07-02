"""Timeline read-model for a repair's chronology.

Fixed ordered list of events; a missing date means the event hasn't happened
yet (or isn't wired to a data source — see ``special_equipment``).
"""

import datetime

from pydantic import BaseModel


class RepairTimelineEventDTO(BaseModel):
    code: str
    label: str
    date: datetime.date | None = None


class RepairTimelineDTO(BaseModel):
    repair_id: int
    events: list[RepairTimelineEventDTO]
