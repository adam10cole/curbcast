from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from curbcast.model.hits import month_files

WARNING_KINDS: dict[str, str] = {
    "P159": "rush_hour_am",
    "P259": "rush_hour_pm",
    "P173": "street_cleaning",
    "P057": "restricted_hours",
    "P046": "school_zone",
}

WARNING_MESSAGES: dict[str, str] = {
    "rush_hour_am": "Morning rush-hour restriction. "
    "No stopping or standing. Vehicles may be towed.",
    "rush_hour_pm": "Evening rush-hour restriction. "
    "No stopping or standing. Vehicles may be towed.",
    "street_cleaning": "Street cleaning restriction: check posted day and hours.",
    "restricted_hours": "Parking is prohibited during posted hours.",
    "school_zone": "School zone restriction during posted school hours.",
}


@dataclass(frozen=True)
class WarningRules:
    min_dates: int = 3  # distinct dates with a ticket in that slot
    recent_days: int = 365  # latest ticket must be recent
    min_season_share: float = 0.01  # rel check if in season


def load_scheduled_events(
    clean_root: Path,
    start: date,
    end: date,
    excluded_months: frozenset[str],
    location_source: str = "xy",
) -> pd.DataFrame:
    files = [str(path) for path in month_files(clean_root, start, end, excluded_months)]
    with duckdb.connect() as con:
        con.read_parquet(files).create_view("tickets")
        events: pd.DataFrame = con.sql(
            """
            SELECT DISTINCT x, y, local_date, weekday, hour, violation_code
            FROM (
                SELECT x, y,
                       CAST(issued_at_utc AT TIME ZONE 'America/New_York' AS DATE) AS local_date,
                       issued_local_weekday AS weekday,
                       issued_local_hour AS hour,
                       violation_code,
                       location_source
                FROM tickets
            )
            WHERE violation_code IN (SELECT unnest(?))
              AND location_source = ?
              AND local_date BETWEEN ? AND ?
            """,
            params=[sorted(WARNING_KINDS), location_source, start, end],
        ).df()

    events["kind"] = events["violation_code"].map(WARNING_KINDS)
    events["local_date"] = pd.to_datetime(events["local_date"])
    return events.drop(columns="violation_code").drop_duplicates(ignore_index=True)


def active_months(events: pd.DataFrame, min_share: float) -> pd.DataFrame:
    monthly = events.assign(month=events["local_date"].dt.month)
    share = monthly.groupby(["kind", "month"]).size() / monthly.groupby("kind").size()
    in_season = share[share >= min_share].reset_index()[["kind", "month"]]
    seasons = {
        kind: sorted(int(month) for month in group["month"])
        for kind, group in in_season.groupby("kind")
    }
    return pd.DataFrame({"kind": list(seasons), "active_months": list(seasons.values())})


def find_warnings(
    events: pd.DataFrame, cutoff: date, rules: WarningRules = WarningRules()
) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(
            columns=[
                "x",
                "y",
                "kind",
                "weekday",
                "hour",
                "dates_seen",
                "last_seen",
                "active_months",
            ]
        )

    slots = events.groupby(["x", "y", "kind", "weekday", "hour"], as_index=False).agg(
        dates_seen=("local_date", "nunique"),
        last_seen=("local_date", "max"),
    )
    recent_since = pd.Timestamp(cutoff) - pd.Timedelta(days=rules.recent_days)
    qualifies = (slots["dates_seen"] >= rules.min_dates) & (slots["last_seen"] >= recent_since)

    warnings = slots[qualifies].merge(
        active_months(events, rules.min_season_share), on="kind", how="inner"
    )
    warnings["last_seen"] = warnings["last_seen"].dt.date
    return warnings.reset_index(drop=True)
