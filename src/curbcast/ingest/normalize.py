from dataclasses import dataclass
import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Violation:
    ticket_number: str
    issued_at_utc: datetime
    issued_local_hour: int
    issued_local_weekday: int
    violation_code: str
    violation_desc: str
    agency_code: str
    fine_amount: Decimal
    latitude: float | None
    longitude: float | None
    is_geocoded: bool
    source_month: str
    source_bjectid: int

@dataclass(frozen=True)
class Reject: 
    source_objectid: int | None
    reason: str
    raw: dict