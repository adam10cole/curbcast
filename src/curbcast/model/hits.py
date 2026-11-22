import csv
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from curbcast.model.params import ModelParams

CATEGORIES = {"patrol", "vehicle", "location", "scheduled", "exclude", "unknown"}


class HitsError(Exception): ...


def load_codes(categories_csv: Path, target_categories: frozenset[str]) -> frozenset[str]:
    unknown_targets = target_categories - CATEGORIES
    if unknown_targets:
        raise HitsError(f"unknown target categories: {unknown_targets}")

    seen: set[str] = set()
    targets: set[str] = set()

    with categories_csv.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        missing = {"violation_code", "category"} - set(reader.fieldnames or [])
        if missing:
            raise HitsError(f"{categories_csv} is missing colums: {missing}")

        for row in reader:
            code, category = row["violation_code"].strip(), row["category"].strip()
            if code in seen:
                raise HitsError(f"duplicate code {code}")
            if category not in CATEGORIES:
                raise HitsError(f"unknown category {category!r}")
            seen.add(code)
            if category in target_categories:
                targets.add(code)
    if not targets:
        raise HitsError(f"no codes in {categories_csv}")

    return frozenset(targets)


def month_files(clean_root: Path, start: date, end: date, excluded: frozenset[str]) -> list[Path]:
    files: list[Path] = []
    missing: list[str] = []

    for period in pd.period_range(start, end, freq="M"):
        month = str(period)
        if month in excluded:
            continue
        path = (
            clean_root / f"year={period.year}" / f"month={period.month:02d}" / "violations.parquet"
        )
        if path.exists():
            files.append(path)
        else:
            missing.append(month)
    if missing:
        raise HitsError(f"missing clean data for months: {', '.join(missing)}")
    return files


def load_hits(
    clean_root: Path, categories_csv: Path, params: ModelParams, start: date, end: date
) -> pd.DataFrame:
    if start > end:
        raise HitsError(f"start {start} is after {end}")

    codes = load_codes(categories_csv, params.target_categories)
    files = [str(path) for path in month_files(clean_root, start, end, params.excluded_months)]

    with duckdb.connect() as con:
        con.read_parquet(files).create_view("tickets")
        hits: pd.DataFrame = con.sql(
            """
            SELECT DISTINCT x, y, local_date, weekday, hour
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
            ORDER BY local_date, x, y, hour
            """,
            params=[sorted(codes), params.location_source, start, end],
        ).df()

    return hits
