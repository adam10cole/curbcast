import os
from pathlib import Path

import psycopg

from curbcast.ingest.load import load_month

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    result = load_month(conn, Path("data/clean/year=2026/month=09"))
    print(result)
    result = load_month(conn, Path("data/clean/year=2016/month=09"))
    print(result)