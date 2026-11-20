import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

TIME_12H = re.compile(r"^(\d{1,2}):(\d{2})\s?([AP]M)$", re.IGNORECASE)
TIME_HHMM = re.compile(r"^\d{1,4}$")

DC_TIMEZONE = ZoneInfo("America/New_York")
DC_BOUNDARIES = (-77.12, 38.79, -76.91, 39.00)  # (min long, lat, max long, lat)


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
    field: str
    reason: str
    raw: dict[str, Any]


def parse_ticket_number(value: object) -> str:
    return str(_require(value, "TICKET_NUMBER", str, int)).strip()


def parse_issue_date(value: object) -> date:
    millis = _require(value, "ISSUE_DATE", int)
    try:
        moment = datetime.fromtimestamp(millis / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise FieldError("ISSUE_DATE", f"not valid timestamp: {value!r}") from exc

    issued = moment.astimezone(DC_TIMEZONE).date()
    if not 200 <= issued.year <= datetime.now(UTC).year + 1:
        raise FieldError("ISSUE_DATE", f"not a possible year {issued.year}: {value!r}")

    return issued


def parse_issue_time(value: object) -> time:
    text = str(_require(value, "TICKET_NUMBER", str, int)).strip()

    if match := TIME_12H.match(text):
        hour, minute = int(match[1]), int(match[2])
        if not 1 <= hour <= 12:
            raise FieldError("ISSUE_TIME", f"12 hour value out of range: {value!r}")
        hour = hour % 12 + (12 if match[3].upper() == "PM" else 0)
    elif TIME_HHMM.match(text):
        hour, minute = divmod(int(text), 100)
    else:
        raise FieldError("ISSUE_TIME", f"unexpected format: {value!r}")

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise FieldError("ISSUE_TIME", f"out of the range: {value!r}")

    return time(hour, minute)


def parse_text(value: object, field: str) -> str:
    text = str(_require(value, field, str, int))
    return "".join(text.split())


def parse_fine(value: object) -> Decimal:
    amount = _require(value, "FINE_AMOUNT", int, float)

    try:
        fine = Decimal(str(amount))
    except InvalidOperation as exc:
        raise FieldError("FINE_AMOUNT", f"not a number: {value!r}") from exc

    if not fine.is_finite():
        raise FieldError("FINE_AMOUNT", f"not a finite number: {value!r}")
    if fine < 0:
        raise FieldError("FINE_AMOUNT", f"negative: {value!r}")

    return fine.quantize(Decimal("0.01"))


def parse_coords(lat: object, lon: object) -> tuple[float | None, float | None, bool]:
    latitude, longitude = _as_coordinate(lat), _as_coordinate(lon)
    if latitude is None or longitude is None:
        return None, None, False

    min_lon, min_lat, max_lon, max_lat = DC_BOUNDARIES
    if not (min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon):
        return None, None, False

    return latitude, longitude, True


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


# def normalize_month(raw_dir: Path, clean_dir: Path) -> NormalizeResult:


# Helpers
def _require[T](value: object, field: str, *allowed: type[T]) -> T:
    if value is None:
        raise FieldError(field, "missing")
    if isinstance(value, bool):
        raise FieldError(field, f"unexpected boolean: {value!r}")
    if isinstance(value, str) and not value.strip():
        raise FieldError(field, "empty")
    if not isinstance(value, allowed):
        expected = " or ".join(t.__name__ for t in allowed)
        raise FieldError(field, f"expected {expected} but got {type(value).__name__}: {value!r}")
    return value


def _as_coordinate(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number == 0:
        return None
    return number
