import math
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from curbcast.ingest.normalize import DC_BOUNDARIES
from curbcast.model.holidays import holiday_probablity, weekday_holidays
from curbcast.model.restrictions import WARNING_MESSAGES

DC_TIMEZONE = ZoneInfo("America/New_York")
MAX_DISTANCE = 100.0  # meters
MAX_DURATION = timedelta(hours=24)
ONE_HOUR = timedelta(hours=1)

NEAREST_SEGMENT_SQL = """
    WITH point AS (
        SELECT ST_Transform(ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 26985) AS geom
    )
    SELECT s.id,
           ST_Y(ST_Transform(s.geom, 4326)) AS lat,
           ST_X(ST_Transform(s.geom, 4326)) AS lon,
           ST_Distance(s.geom, point.geom) AS distance_m,
           pr.p
    FROM segments s
    JOIN predictions pr ON pr.segment_id = s.id AND pr.run_id = %(run_id)s
    CROSS JOIN point
    ORDER BY s.geom <-> point.geom
    LIMIT 1
"""


class PredictError(Exception): ...


@dataclass(frozen=True)
class RestrictionWarning:
    kind: str
    message: str
    start: datetime  # DC local time
    end: datetime


@dataclass(frozen=True)
class HourRisk:
    start: datetime  # DC local time
    end: datetime  # DC local time
    p_hour: float  # model probability for whole hour
    exposure: float
    holiday: bool

    @property
    def p(self) -> float:
        return float(1 - (1 - self.p_hour) ** self.exposure)


@dataclass(frozen=True)
class Prediction:
    run_id: int
    model_cutoff: date
    seg_id: int
    seg_lat: float
    seg_lon: float
    distance: float  # meters
    hours: tuple[HourRisk, ...]
    warnings: tuple[RestrictionWarning, ...] = ()

    @property
    def combined(self) -> float:
        return 1 - math.prod(1 - hour.p for hour in self.hours)


@dataclass(frozen=True)
class NotEnoughData:
    reason: str
    distance: float | None = None  # meters


def _split_duration(start: datetime, duration: timedelta) -> list[tuple[datetime, datetime]]:
    current = start.astimezone(UTC)  # IN UTC so no daylight savings ish
    end = current + duration
    pieces: list[tuple[datetime, datetime]] = []
    while current < end:
        next_hour = current.replace(minute=0, second=0, microsecond=0) + ONE_HOUR
        piece_end = min(next_hour, end)
        pieces.append((current, piece_end))
        current = piece_end
    return pieces


def predict(
    conn: psycopg.Connection[Any],
    lat: float,
    lon: float,
    start: datetime,
    duration: timedelta,
) -> Prediction | NotEnoughData:
    if not timedelta(0) < duration <= MAX_DURATION:
        raise PredictError(f"0 < duration < {MAX_DURATION}")

    min_lon, min_lat, max_lon, max_lat = DC_BOUNDARIES
    if not (min_lat <= lat <= max_lat and min_lon < lon < max_lon):
        return NotEnoughData("outside DC")

    local_start = (
        start.replace(tzinfo=DC_TIMEZONE) if start.tzinfo is None else start.astimezone(DC_TIMEZONE)
    )

    run = conn.execute(
        "SELECT id, cutoff_date, holiday_factor FROM model_runs WHERE is_active"
    ).fetchone()
    if run is None:
        raise PredictError("no active model curbcast --help for help")
    run_id, cutoff, holiday_factor = int(run[0]), run[1], float(run[2])

    row = conn.execute(NEAREST_SEGMENT_SQL, {"lon": lon, "lat": lat, "run_id": run_id}).fetchone()
    if row is None:
        return NotEnoughData("active model has no segments")
    seg_id, seg_lat, seg_lon, distance, weekly = row
    if distance > MAX_DISTANCE:
        return NotEnoughData(f"no ticket history within {MAX_DISTANCE} m")

    pieces = _split_duration(local_start, duration)
    first_day = pieces[0][0].astimezone(DC_TIMEZONE).date()
    last_day = pieces[-1][1].astimezone(DC_TIMEZONE).date()
    holidays = weekday_holidays(first_day, last_day)

    hours: list[HourRisk] = []
    for split_start, split_end in pieces:
        local = split_start.astimezone(DC_TIMEZONE)
        p_hour = float(weekly[local.weekday() * 24 + local.hour])
        is_holiday = local.date() in holidays
        if is_holiday:
            p_hour = holiday_probablity(p_hour, holiday_factor)
        hours.append(
            HourRisk(
                start=local,
                end=split_end.astimezone(DC_TIMEZONE),
                p_hour=p_hour,
                exposure=(split_end - split_start) / ONE_HOUR,
                holiday=is_holiday,
            )
        )

    rows = conn.execute(
        "SELECT kind, weekday, hour, active_months FROM restriction_warnings "
        "WHERE run_id = %s AND segment_id = %s",
        (run_id, seg_id),
    ).fetchall()

    return Prediction(
        run_id=run_id,
        model_cutoff=cutoff,
        seg_id=int(seg_id),
        seg_lat=float(seg_lat),
        seg_lon=float(seg_lon),
        distance=float(distance),
        hours=tuple(hours),
        warnings=_stay_warnings(hours, rows),
    )


def _stay_warnings(
    hours: list[HourRisk], rows: list[tuple[str, int, int, list[int]]]
) -> tuple[RestrictionWarning, ...]:

    by_slot: dict[tuple[int, int], list[tuple[str, set[int]]]] = {}
    for kind, weekday, hour, months in rows:
        by_slot.setdefault((int(weekday), int(hour)), []).append((kind, set(months)))

    spans: list[RestrictionWarning] = []
    for piece in hours:
        for kind, season in by_slot.get((piece.start.weekday(), piece.start.hour), []):
            if piece.start.month not in season:
                continue
            if spans and spans[-1].kind == kind and spans[-1].end == piece.start:
                spans[-1] = replace(spans[-1], end=piece.end)
            else:
                spans.append(
                    RestrictionWarning(kind, WARNING_MESSAGES[kind], piece.start, piece.end)
                )
    return tuple(spans)
