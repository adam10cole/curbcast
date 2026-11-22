import argparse
import logging
import sys
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import psycopg

from curbcast.config import Settings
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
from curbcast.model.evaluate import EvaluationError, evaluate
from curbcast.model.fit import FitError, fit
from curbcast.model.hits import HitsError, load_hits
from curbcast.model.holidays import HolidayError, estimate_holiday_factor, weekday_holidays
from curbcast.model.params import ModelParams
from curbcast.model.store import StoreError, activate_run, prune_runs, write_run

log = logging.getLogger("curbcast")
DEFAULT_TRAIN_START = date(2023, 1, 1)
HANDLED_ERRORS = (
    ExtractError,
    NormalizeError,
    LoadError,
    HitsError,
    FitError,
    HolidayError,
    EvaluationError,
    StoreError,
)


def parse_month(text: str) -> MonthRef:
    try:
        year, month = text.split("-")
        return MonthRef(int(year), int(month))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Needs YYYY-MM, not {text!r}") from exc


def parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Needs YYYY-MM-DD, not {text!r}") from exc


def parse_non_negative(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Needs whole number not {text!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("Needs to be 0 or more")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="curbcast", description="DC parking ticket risk map (CLI)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    for name, help_text in [
        ("extract", "download one month of raw data"),
        ("normalize", "clean one month of raw data"),
        ("load", "load one month of cleaned data into Postgres"),
        ("run", "extract, normalize, and load one month"),
    ]:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--month", type=parse_month, required=True, help="YYYY-MM")

    backfill = commands.add_parser("backfill", help="run every month in certain range")
    backfill.add_argument("--start", type=parse_month, required=True, help="YYYY-MM")
    backfill.add_argument("--end", type=parse_month, required=True, help="YYYY-MM")
    backfill.add_argument("--skip-extract", action="store_true", help="reuse raw files")

    fit_parser = commands.add_parser("fit", help="fit the risk model and store its predictions")
    fit_parser.add_argument(
        "--cutoff", type=parse_date, required=True, help="last training date YYYY-MM-DD"
    )
    fit_parser.add_argument(
        "--start",
        type=parse_date,
        default=DEFAULT_TRAIN_START,
        help="first training date YYYY-MM-DD",
    )
    fit_parser.add_argument(
        "--holdout-months",
        type=parse_non_negative,
        default=3,
        help="months before cutoff to score a backtest on (0=skips and default=3)",
    )
    fit_parser.add_argument(
        "--no-activate",
        action="store_true",
        help="store the run without making it active",
    )
    fit_parser.add_argument(
        "--keep",
        type=parse_non_negative,
        default=3,
        help="model runs to keep after activating (0 keeps all and default=3)",
    )
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
            with psycopg.connect(settings.require_database_url(), autocommit=True) as conn:
                do_run(conn, settings, args.month)
        elif args.command == "backfill":
            with psycopg.connect(settings.require_database_url(), autocommit=True) as conn:
                do_backfill(conn, settings, args.start, args.end, skip_extract=args.skip_extract)
        elif args.command == "fit":
            with psycopg.connect(settings.require_database_url(), autocommit=True) as conn:
                do_fit(
                    conn,
                    settings,
                    args.start,
                    args.cutoff,
                    args.holdout_months,
                    activate=not args.no_activate,
                    keep=args.keep,
                )
    except HANDLED_ERRORS as exc:
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
            "UPDATE ingestion_runs SET status = 'failed', finished_at = now(), error = %s "
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
def do_run(
    conn: psycopg.Connection, settings: Settings, month: MonthRef, skip_extract: bool = False
) -> None:
    if not skip_extract:
        with track(conn, month, "extract") as counts:
            extracted = do_extract(settings, month)
            counts.expected, counts.clean = extracted.expected, extracted.fetched

    with track(conn, month, "normalize") as counts:
        normalized = do_normalize(settings, month)
        counts.expected, counts.clean, counts.rejected = (
            normalized.total,
            normalized.clean,
            normalized.rejected,
        )

    with track(conn, month, "load") as counts:
        loaded = do_load(conn, settings, month)
        counts.expected = loaded.violations + loaded.rejects
        counts.clean, counts.rejected = loaded.violations, loaded.rejects


def do_backfill(
    conn: psycopg.Connection,
    settings: Settings,
    start: MonthRef,
    end: MonthRef,
    skip_extract: bool = False,
) -> None:
    for month in month_range(start, end):
        try:
            do_run(conn, settings, month, skip_extract=skip_extract)
        except MonthNotPublishedError:
            log.info("Skipping %s its not published yet", month)
        if not skip_extract:
            time.sleep(2)  # let's try not to get rate limited


def do_fit(
    conn: psycopg.Connection,
    settings: Settings,
    start: date,
    cutoff: date,
    holdout_months: int,
    *,
    activate: bool,
    keep: int,
) -> int:
    params = ModelParams()
    clean = settings.data_dir / "clean"
    categories = settings.data_dir / "reference" / "violation_categories.csv"

    evaluation: dict[str, object] | None = None
    if holdout_months > 0:
        eval_start = (
            pd.Timestamp(cutoff) + pd.Timedelta(days=1) - pd.DateOffset(months=holdout_months)
        ).date()
        train_end = eval_start - timedelta(days=1)
        if train_end < start:
            raise FitError(f"holdout of {holdout_months} months leave no training data")

        train_hits = load_hits(clean, categories, params, start, train_end)
        test_hits = load_hits(clean, categories, params, eval_start, cutoff)
        backtest = fit(train_hits, start, train_end, params)
        backtest_factor = estimate_holiday_factor(
            train_hits, start, train_end, params.excluded_months, weekday_holidays(start, train_end)
        )

        result = evaluate(
            backtest,
            backtest_factor,
            train_hits,
            start,
            train_end,
            test_hits,
            eval_start,
            cutoff,
            weekday_holidays(eval_start, cutoff),
            params,
        )
        log.info(
            "Backtest %s..%s: %.2f%% better than null, %.2f%% than place only, "
            "%.2f%% than time only, and top-1%% capture %.1f%%",
            eval_start,
            cutoff,
            result.vs_null * 100,
            result.vs_place_only * 100,
            result.vs_time_only * 100,
            result.capture[0.01] * 100,
        )
        evaluation = result.to_json()

    hits = load_hits(clean, categories, params, start, cutoff)
    predictions = fit(hits, start, cutoff, params)
    factor = estimate_holiday_factor(
        hits, start, cutoff, params.excluded_months, weekday_holidays(start, cutoff)
    )
    run_id = write_run(
        conn,
        params=params,
        train_start=start,
        cutoff=cutoff,
        holiday_factor=factor,
        predictions=predictions,
        evaluation=evaluation,
    )
    log.info(
        "Stored model run %d: %s..%s, %d segments, holiday factor %.4f",
        run_id,
        start,
        cutoff,
        len(predictions) // 168,
        factor,
    )

    if activate:
        activate_run(conn, run_id)
        log.info("Model run %d now active", run_id)
        if keep > 0:
            deleted = prune_runs(conn, keep=keep)
            if deleted:
                log.info("Pruned %d old model runs", deleted)

    return run_id


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
