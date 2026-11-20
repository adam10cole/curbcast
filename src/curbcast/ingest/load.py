from calendar import month
from dataclasses import dataclass
from typing import Any
from zipfile import Path
from curbcast.ingest.normalize import REJECT_SCHEMA, VIOLATION_SCHEMA
import pyarrow.parquet as pq
import pyarrow.compute as pc
import psycopg

VIOLATION_COLUMNS = VIOLATION_SCHEMA.names

# Didn't make the parquet and the db table column names match exactly... 
# ugh so do it manually
REJECT_PARQUET_COLUMNS = ["source_month", "source_objectid", "field", "reason", "raw_json"]
REJECT_TABLE_COLUMNS = ["source_month", "source_objectid", "field", "reason", "raw"]

class LoadError(Exception): ...

@dataclass(frozen=True)
class LoadResult:
    month: str
    violations: int
    rejects: int

def load_month(conn: psycopg.Connection, clean_dir: Path) -> LoadResult:
    violations = pq.read_table(clean_dir / "violations.parquet")
    rejects = pq.read_table(clean_dir / "rejects.parquet")

    # make sure we're only dealing with one month
    months = set(pc.unique(violations["source_month"]).to_pylist()) | set (
        pc.unique(rejects["source_month"]).to_pylist()
    )
    if len(months) != 1:
        raise LoadError(f"{clean_dir}: more than one month found within the data")
    month = months.pop()

    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM violations WHERE source_month = %s", (month,))
        cur.execute("DELETE FROM violation_rejects WHERE source_month = %s", (month,))

        with cur.copy(f"COPY violations ({', '.join(VIOLATION_COLUMNS)}) FROM STDIN") as copy:
            for row in violations.to_pylist():
                copy.write_row([row[col] for col in VIOLATION_COLUMNS])

        with cur.copy(f"COPY violation_rejects ({', '.join(REJECT_TABLE_COLUMNS)}) FROM STDIN") as copy:
            for row in rejects.to_pylist():
                copy.write_row([row[col] for col in REJECT_PARQUET_COLUMNS])

        cur.execute("SELECT count(*) FROM violations WHERE source_month = %s", (month,))
        count_violations = _fetch_count(cur)
        cur.execute("SELECT count(*) FROM violation_rejects WHERE source_month = %s", (month,))
        count_rejects = _fetch_count(cur)

        if count_violations != violations.num_rows:
            raise LoadError(
                f"{month}: loaded {count_violations} violations, file has {violations.num_rows}"
            )
        if count_rejects != rejects.num_rows:
            raise LoadError(
                f"{month}: loaded {count_rejects} rejects, file has {rejects.num_rows}"
            )

        return LoadResult(
            month=month,
            violations=count_violations,
            rejects=count_rejects
        )

# Helpers
def _fetch_count(cur: psycopg.Cursor[tuple[Any, ...]]) -> int:
    row = cur.fetchone()
    if row is None:
        raise LoadError("count query returned no rows")
    return int(row[0])