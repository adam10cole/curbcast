import dataclasses
import hashlib
import json
import logging
import math
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import Transformer

TIME_12H = re.compile(r"^(\d{1,2}):(\d{2})\s?([AP]M)$", re.IGNORECASE)
TIME_HHMM = re.compile(r"^\d{1,4}$")

_TO_WGS84 = Transformer.from_crs("EPSG:26985", "EPSG:4326", always_xy=True)

DC_TIMEZONE = ZoneInfo("America/New_York")
DC_BOUNDARIES = (-77.12, 38.79, -76.91, 39.00)  # (min long, lat, max long, lat)

log = logging.getLogger(__name__)
MAX_REJECT_RATE = 0.01

VIOLATION_SCHEMA = pa.schema(
    [
        ("ticket_number", pa.string()),
        ("issued_at_utc", pa.timestamp("us", tz="UTC")),
        ("issued_local_hour", pa.int8()),
        ("issued_local_weekday", pa.int8()),
        ("violation_code", pa.string()),
        ("violation_desc", pa.string()),
        ("agency_code", pa.string()),
        ("fine_amount", pa.decimal128(10, 2)),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("x", pa.float64()),
        ("y", pa.float64()),
        ("location_source", pa.string()),
        ("is_geocoded", pa.bool_()),
        ("source_month", pa.string()),
        ("source_objectid", pa.int64()),
    ]
)

REJECT_SCHEMA = pa.schema(
    [
        ("source_month", pa.string()),
        ("source_objectid", pa.int64()),
        ("field", pa.string()),
        ("reason", pa.string()),
        ("raw_json", pa.string()),
    ]
)


class NormalizeError(Exception): ...


class RawDataError(NormalizeError): ...


class RowCountError(NormalizeError): ...


class RejectRateError(NormalizeError): ...


class FieldError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class Location:
    lat: float | None
    long: float | None
    x: float | None
    y: float | None
    source: str | None  # xy or latlong


@dataclass(frozen=True)
class Violation:
    ticket_number: str
    issued_at_utc: datetime
    issued_local_hour: int
    issued_local_weekday: int
    violation_code: str
    violation_desc: str | None
    agency_code: str
    fine_amount: Decimal | None
    latitude: float | None
    longitude: float | None
    x: float | None
    y: float | None
    location_source: str | None
    is_geocoded: bool
    source_month: str
    source_objectid: int


@dataclass(frozen=True)
class Reject:
    source_month: str
    source_objectid: int | None
    field: str
    reason: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class NormalizeResult:
    month: str
    total: int
    clean: int
    rejected: int
    geocoded: int
    out_dir: Path


def parse_ticket_number(value: object) -> str:
    return str(_require(value, "TICKET_NUMBER", str, int)).strip()


def parse_issue_date(value: object) -> date:
    millis = _require(value, "ISSUE_DATE", int)
    try:
        moment = datetime.fromtimestamp(millis / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise FieldError("ISSUE_DATE", f"not valid timestamp: {value!r}") from exc

    issued = moment.astimezone(DC_TIMEZONE).date()
    if not 2000 <= issued.year <= datetime.now(UTC).year + 1:
        raise FieldError("ISSUE_DATE", f"not a possible year {issued.year}: {value!r}")

    return issued


def parse_issue_time(value: object) -> time:
    text = str(_require(value, "ISSUE_TIME", str, int)).strip()

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
    return " ".join(text.split())


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


def parse_location(x: object, y: object, lat: object, long: object) -> Location:
    px, py = _as_coordinate(x), _as_coordinate(y)
    if px is not None and py is not None:
        long_xy, lat_xy = _TO_WGS84.transform(px, py)
        if _in_dc(lat_xy, long_xy):
            return Location(lat_xy, long_xy, px, py, "xy")

    plat, plong = _as_coordinate(lat), _as_coordinate(long)
    if plat is not None and plong is not None and _in_dc(plat, plong):
        return Location(plat, plong, None, None, "latlong")

    return Location(None, None, None, None, None)


def normalize_record(attrs: Mapping[str, Any], source_month: str) -> Violation | Reject:
    try:
        ticket_number = parse_ticket_number(attrs.get("TICKET_NUMBER"))
        issued_local = datetime.combine(
            parse_issue_date(attrs.get("ISSUE_DATE")),
            parse_issue_time(attrs.get("ISSUE_TIME")),
            tzinfo=DC_TIMEZONE,
        )
        violation_code = parse_text(attrs.get("VIOLATION_CODE"), "VIOLATION_CODE").upper()
        violation_desc = _optional(
            attrs.get("VIOLATION_PROC_DESC"), lambda v: parse_text(v, "VIOLATION_PROC_DESC")
        )
        agency_code = parse_text(attrs.get("ISSUING_AGENCY_CODE"), "ISSUING_AGENCY_CODE")
        fine_amount = _optional(attrs.get("FINE_AMOUNT"), parse_fine)
        location = parse_location(
            attrs.get("XCOORD"), attrs.get("YCOORD"), attrs.get("LATITUDE"), attrs.get("LONGITUDE")
        )
    except FieldError as exc:
        return Reject(
            source_month=source_month,
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
        latitude=location.lat,
        longitude=location.long,
        x=location.x,
        y=location.y,
        location_source=location.source,
        is_geocoded=location.source is not None,
        source_month=source_month,
        source_objectid=attrs["OBJECTID"],
    )


def normalize_month(raw_dir: Path, clean_dir: Path) -> NormalizeResult:
    manifest = _load_manifest(raw_dir)
    month = str(manifest["month"])

    violations: list[Violation] = []
    rejects: list[Reject] = []
    for page in manifest["pages"]:
        for attrs in _read_verified_page(raw_dir, page):
            outcome = normalize_record(attrs, month)
            if isinstance(outcome, Reject):
                rejects.append(outcome)
            else:
                violations.append(outcome)

    total = len(violations) + len(rejects)
    if total != manifest["fetched_count"]:
        raise RowCountError(
            f"{month}: processed {total} rows but manifest has {manifest['fetched_count']} rows"
        )

    if total == 0:
        raise RawDataError(f"{month}: no rows in {raw_dir}")

    reject_rate = len(rejects) / total
    if reject_rate > MAX_REJECT_RATE:
        raise RejectRateError(f"{month}: exceeded the max reject rate")

    work_dir = clean_dir.with_name(clean_dir.name + ".partial")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    pq.write_table(_violations_table(violations), work_dir / "violations.parquet")
    pq.write_table(_rejects_table(rejects), work_dir / "rejects.parquet")

    if clean_dir.exists():
        shutil.rmtree(clean_dir)
    work_dir.rename(clean_dir)

    result = NormalizeResult(
        month=month,
        total=total,
        clean=len(violations),
        rejected=len(rejects),
        geocoded=sum(v.is_geocoded for v in violations),
        out_dir=clean_dir,
    )
    log.info(
        "Normalized %s: %d clean and %d rejected (%.2f%%)",
        month,
        result.clean,
        result.rejected,
        reject_rate * 100,
    )
    return result


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


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _optional[T](value: object, parse: Callable[[object], T]) -> T | None:
    return None if _is_missing(value) else parse(value)


def _load_manifest(raw_dir: Path) -> dict[str, Any]:
    path = raw_dir / "manifest.json"
    if not path.exists():
        raise RawDataError(f"No manifest in {raw_dir}")
    manifest: dict[str, Any] = json.loads(path.read_text())
    return manifest


def _read_verified_page(raw_dir: Path, page: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = (raw_dir / page["file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != page["sha256"]:
        raise RawDataError(f"hash error for {page['file']} in {raw_dir}")
    payload = json.loads(data)
    return [feature["attributes"] for feature in payload["features"]]


def _violations_table(rows: list[Violation]) -> pa.Table:
    return pa.Table.from_pylist([dataclasses.asdict(r) for r in rows], schema=VIOLATION_SCHEMA)


def _rejects_table(rows: list[Reject]) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "source_month": r.source_month,
                "source_objectid": r.source_objectid,
                "field": r.field,
                "reason": r.reason,
                "raw_json": json.dumps(r.raw, sort_keys=True),
            }
            for r in rows
        ],
        schema=REJECT_SCHEMA,
    )


def _in_dc(lat: float, long: float) -> bool:
    min_long, min_lat, max_long, max_lat = DC_BOUNDARIES
    return min_lat <= lat <= max_lat and min_long <= long <= max_long
