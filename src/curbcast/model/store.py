from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from curbcast.model.params import ModelParams

WEEKDAY_HOURS = 168


class StoreError(Exception): ...


def weekly_arrays(predictions: pd.DataFrame) -> pd.DataFrame:
    ordered = predictions.sort_values(["x", "y", "weekday", "hour"], ignore_index=True)
    if len(ordered) == 0 or len(ordered) % WEEKDAY_HOURS:
        raise StoreError(f"{len(ordered)} prediction rows is not a whole number of weeks")

    n_segments = len(ordered) // WEEKDAY_HOURS
    slot_index = ordered["weekday"].to_numpy() * 24 + ordered["hour"].to_numpy()
    if not np.array_equal(slot_index, np.tile(np.arange(WEEKDAY_HOURS), n_segments)):
        raise StoreError("each segment needs every weekday-hour exactly once")

    xs = ordered["x"].to_numpy().reshape(n_segments, WEEKDAY_HOURS)
    ys = ordered["y"].to_numpy().reshape(n_segments, WEEKDAY_HOURS)
    if not ((xs == xs[:, :1]).all() and (ys == ys[:, :1]).all()):
        raise StoreError("prediction rows for a segment are not contiguous")

    return pd.DataFrame(
        {
            "x": xs[:, 0],
            "y": ys[:, 0],
            "p": list(ordered["p"].to_numpy().reshape(n_segments, WEEKDAY_HOURS)),
        }
    )


def _upsert_segments(
    cur: psycopg.Cursor[Any], weekly: pd.DataFrame
) -> dict[tuple[float, float], int]:
    cur.execute(
        "CREATE TEMP TABLE incoming_segments (x double precision, y double precision) "
        "ON COMMIT DROP"
    )
    with cur.copy("COPY incoming_segments (x, y) FROM STDIN") as copy:
        for x, y in zip(weekly["x"], weekly["y"], strict=True):
            copy.write_row((float(x), float(y)))

    cur.execute(
        "INSERT INTO segments (x, y) SELECT x, y FROM incoming_segments "
        "ON CONFLICT (x, y) DO NOTHING"
    )
    cur.execute("SELECT s.x, s.y, s.id FROM segments s JOIN incoming_segments i USING (x, y)")
    ids = {(float(x), float(y)): int(segment_id) for x, y, segment_id in cur.fetchall()}
    if len(ids) != len(weekly):
        raise StoreError(f"matched {len(ids)} segment ids for {len(weekly)} segments")
    return ids


def write_run(
    conn: psycopg.Connection[Any],
    *,
    params: ModelParams,
    train_start: date,
    cutoff: date,
    holiday_factor: float,
    predictions: pd.DataFrame,
    evaluation: dict[str, object] | None,
) -> int:
    weekly = weekly_arrays(predictions)

    with conn.transaction(), conn.cursor() as cur:
        ids = _upsert_segments(cur, weekly)

        cur.execute(
            "INSERT INTO model_runs "
            "(train_start, cutoff_date, params, holiday_factor, n_segments, evaluation) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                train_start,
                cutoff,
                Jsonb(params.to_json()),
                holiday_factor,
                len(weekly),
                Jsonb(evaluation) if evaluation is not None else None,
            ),
        )
        row = cur.fetchone()
        if row is None:
            raise StoreError("model_runs insert returned no id")
        run_id = int(row[0])

        with cur.copy("COPY predictions (run_id, segment_id, p) FROM STDIN") as copy:
            copy.set_types(["int8", "int8", "float8[]"])
            for x, y, p in zip(weekly["x"], weekly["y"], weekly["p"], strict=True):
                copy.write_row((run_id, ids[(float(x), float(y))], [float(v) for v in p]))

        cur.execute("SELECT count(*) FROM predictions WHERE run_id = %s", (run_id,))
        count = cur.fetchone()
        if count is None or int(count[0]) != len(weekly):
            raise StoreError(f"run {run_id}: wrote {count} prediction rows, expected {len(weekly)}")

    return run_id


def activate_run(conn: psycopg.Connection[Any], run_id: int) -> None:
    with conn.transaction():
        conn.execute("UPDATE model_runs SET is_active = false WHERE is_active")
        cur = conn.execute("UPDATE model_runs SET is_active = true WHERE id = %s", (run_id,))
        if cur.rowcount != 1:
            raise StoreError(f"no model run with id {run_id}")


def prune_runs(conn: psycopg.Connection[Any], keep: int = 3) -> int:
    if keep < 1:
        raise StoreError("keep must be at least 1")
    with conn.transaction():
        cur = conn.execute(
            "DELETE FROM model_runs WHERE NOT is_active AND id NOT IN "
            "(SELECT id FROM model_runs ORDER BY id DESC LIMIT %s)",
            (keep,),
        )
        return cur.rowcount