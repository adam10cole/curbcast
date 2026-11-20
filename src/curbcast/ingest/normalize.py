from collections.abc import Mapping
from dataclasses import dataclass, field
import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from datetime import UTC, date, datetime, time

DC_TIMEZONE = ZoneInfo("America/New Yokr")
DC_BOUNDARIES = (-77.12, 38.79, -76.91, 39.00)      # (min long, lat, max long, lat)

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
    source_objectid: int

@dataclass(frozen=True)
class Reject: 
    source_objectid: int | None
    reason: str
    raw: dict

def parse_ticket_number(value: object) -> str: ...
def parse_issue_date(value: object) -> date: ...
def parse_issue_time(value: object) -> time: ...
def parse_text(value: object, field: str) -> str: ...
def parse_fine(value: object) -> Decimal: ...
def parse_coords(lat: object, lon: object) -> tuple[float | None, float | None, bool]: ...

def normalize_record(attrs: Mapping[str, Any], source_month: str) -> Violation | Reject:
    try:
        ticket_number = parse_ticket_number(attrs.get("TICKET_NUMBER"))
        issued_local = datetime.combine(
            parse_issue_date(attrs.get("ISSUE_DATE")),
            parse_issue_time(attrs.get("ISSUE_TIME")),
            tzinfo=DC_TIMEZONE,
        )
        violation_code = parse_text(attrs.get("VIOLATION_CODE"), "VIOLATION_CODE").upper()
        violation_desc = parse_text(attrs.get("VIOLATION_PROC_DESC"), "VIOLATION_PROC_DESC")
        agency_code = parse_text(attrs.get("ISSUING_AGENCY_CODE"), "ISSUING_AGENCY_CODE")
        fine_amount = parse_fine(attrs.get("FINE_AMOUNT"))
        latitude, longitude, is_geocoded = parse_coords(
            attrs.get("LATITUDE"), attrs.get("LONGITUDE")
        )
    except FieldError as exc:
        return Reject(
            source_objectid=attrs.get("OBJECTID"),
            field=exc.field,
            reason=exc.reason,
            raw=dict(attrs),
        )

    return Violation(
        ticket_number=ticket_number,
        issued_at_utc=issued_local.astimezone(UTC),
        issued_local_hour=issued_local.hour,
        issued_local_weekday=issued_local.weekday(),
        violation_code=violation_code,
        violation_desc=violation_desc,
        agency_code=agency_code,
        fine_amount=fine_amount,
        latitude=latitude,
        longitude=longitude,
        is_geocoded=is_geocoded,
        source_month=source_month,
        source_objectid=attrs["OBJECTID"],
    )

#def normalize_month(raw_dir: Path, clean_dir: Path) -> NormalizeResult: 