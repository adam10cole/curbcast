import argparse
import logging
import os
import sys
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psycopg

from curbcast.ingest.extract import (
    ExtractError,
    ExtractResult,
    MonthNotPublishedError,
    MonthRef,
    build_session,
    extract_month,
)
from curbcast.ingest.load import LoadError, LoadResult, load_month
from curbcast.ingest.normalize import NormalizeError, NormalizeResult, normalize_month

log = logging.getLogger("curbcast")

DEFAULT_ARCGIS_BASE_URL = "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA"

@dataclass(frozen=True)
class Settings:
    data_dir: Path
    arcgis_base_url: str
    database_url: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "data")),
            arcgis_base_url=os.environ.get("ARCGIS_BASE_URL", DEFAULT_ARCGIS_BASE_URL),
            database_url=os.environ.get("DATABASE_URL"),
        )

    def require_database_url(self) -> str:
        if not self.database_url:
            raise SystemExit("DATABASE_URL is not set or not running with .env file")
        return self.database_url

def parse_month(text: str) -> MonthRef:
    try: 
        year, month = text.split("-")
        return MonthRef(int(year), int(month))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Needs YYYY-MM, not {text!r}") from exc

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="curbcast", description="DC " \
    "parking ticket risk map (CLI)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    for name, help_text in [
        ("extract", "download one month of raw data"),
        ("normalize", "clean one month of raw data"),
        ("load", "load one month of cleaned data into Postgres"),
        ("run", "extract, normalize, and load one month")
    ]:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--month", type=parse_month, required=True, help="YYYY-MM")

    backfill = commands.add_parser("backfill", help="run every month in certain range")
    backfill.add_argument("--start", type=parse_month, required=True, help="YYYY-MM")
    backfill.add_argument("--end", type=parse_month, required=True, help="YYYY-MM")
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    ) 
    settings = Settings.from_env()

    try:
        if args.command == "extract":
            do_extract(settings, args.month)
        elif args.command == "normalize":
            do_normalize(settings, args.month)
        elif args.command == "load":
            with psycopg.connect(settings.require_database_url(), autocommit=True) as conn:
                do_load(conn, settings, args.month)
        elif args.command == "run":
            settings.require_database_url()
            with psycopg.connect(settings.require_database_url(), autocommit=True) as conn:
                do_run(conn, settings, args.month)
        elif args.command == "backfill":
            settings.require_database_url()
            with psycopg.connect(settings.require_database_url(), autocommit=True) as conn:
                do_backfill(conn, settings, args.month, args.month)
    except (ExtractError, NormalizeError, LoadError) as exc:
        log.error("%s", exc)
        return 1
    return 0


# Helpers
def do_extract(settings: Settings, month: MonthRef) -> ExtractResult:
    return extract_month(build_session(), settings.arcgis_base_url, month, settings.data_dir)


def do_normalize(settings: Settings, month: MonthRef) -> NormalizeResult:
    return normalize_month(raw_dir(settings, month), clean_dir(settings, month))


def do_load(conn: psycopg.Connection, settings: Settings, month: MonthRef) -> LoadResult:
    return load_month(conn, clean_dir(settings, month))


@dataclass
class StageCounts:
    expected: int | None = None
    clean: int | None = None
    rejected: int | None = None


@contextmanager
def track(conn: psycopg.Connection, month: MonthRef, stage: str) -> Generator[StageCounts]:
    row = conn.execute(
        "INSERT INTO ingestion_runs (source_month, stage, status) "
        "VALUES (%s, %s, 'running') RETURNING id",
        (str(month), stage),
    ).fetchone()
    assert row is not None
    run_id = row[0]
    counts = StageCounts()
    try:
        yield counts
    except Exception as exc:
        conn.execute(
            "UPDATE ingestion_runs SET status = 'failed', finished_at = now(), error = %s"
            "WHERE id = %s",
            (str(exc), run_id),
        )
        raise
    conn.execute(
        "UPDATE ingestion_runs SET status = 'succeeded', finished_at = now(), "
        "rows_expected = %s, rows_clean = %s, rows_rejected = %s WHERE id = %s",
        (counts.expected, counts.clean, counts.rejected, run_id),
    )


# Working meanings for the injection table (TODO: move to README when it's written)
# Stage      | rows_expected    | rows_clean        | rows_rejected
# extract    | server count     | rows fetched      | null
# normalize  | rows in raw files| clean rows        | rejected rows
# load       | rows in parquet  | violations loaded | rejects loaded
def do_run(conn: psycopg.Connection, settings: Settings, month: MonthRef) -> None:
    # cur.execute("DELETE FROM violations WHERE source_month = %s", (month,))
    with track(conn, month, "extract") as counts:
        extracted = do_extract(settings, month)
        counts.expected, counts.clean = extracted.expected, extracted.fetched

    with track(conn, month, "normalize") as counts:
        normalized = do_normalize(settings, month)
        counts.expected, counts.clean, counts.rejected = (
            normalized.total, normalized.clean, normalized.rejected
        )

    with track(conn, month, "load") as counts:
        loaded = do_load(conn, settings, month)
        counts.expected = loaded.violations + loaded.rejects
        counts.clean, counts.rejected = loaded.violations, loaded.rejects


def do_backfill(conn: psycopg.Connection, settings: Settings, 
                start: MonthRef, end: MonthRef) -> None:
    for month in month_range(start, end):
        try:
            do_run(conn, settings, month)
        except MonthNotPublishedError:
            log.info("Skipping %s its not published yet", month)
        time.sleep(2)  # let's try not to get rate limited

def raw_dir(settings: Settings, month: MonthRef) -> Path:
    return settings.data_dir / "raw" / f"year={month.year}" / f"month={month.month:02d}"


def clean_dir(settings: Settings, month: MonthRef) -> Path:
    return settings.data_dir / "clean" / f"year={month.year}" / f"month={month.month:02d}"


def month_range(start: MonthRef, end: MonthRef) -> Iterator[MonthRef]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield MonthRef(year, month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

if __name__ == "__main__":
    sys.exit(main())