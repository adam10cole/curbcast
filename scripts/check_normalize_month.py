from pathlib import Path

from curbcast.ingest.normalize import normalize_month

print(normalize_month(
    Path("data/raw/year=2016/month=09"),
    Path("data/clean/year=2016/month=09"),
))