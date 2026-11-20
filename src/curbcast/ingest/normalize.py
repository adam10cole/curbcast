from dataclasses import dataclass
import datetime
from decimal import Decimal

class FieldError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason

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

def normalize_record(attrs: dict, source_month: str) -> Violation | Reject:
    ...

#def normalize_month(raw_dir: Path, clean_dir: Path) -> NormalizeResult: 