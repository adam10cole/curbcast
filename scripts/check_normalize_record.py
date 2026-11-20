import json
from collections import Counter
from pathlib import Path

from curbcast.ingest.normalize import Reject, normalize_record

for month in ("year=2016/month=09", "year=2026/month=09"):
    ok = 0
    rejects: Counter[tuple[str, str]] = Counter()
    examples: dict[str, object] = {}
    wrong_month = geocoded = 0
    expected_month = int(month[-2:])

    for page in sorted(Path(f"data/raw/{month}").glob("page_*.json")):
        for feature in json.loads(page.read_text())["features"]:
            result = normalize_record(feature["attributes"], month)
            if isinstance(result, Reject):
                rejects[(result.field, result.reason.split(":")[0])] += 1
                examples.setdefault(result.field, result.raw.get(result.field))
            else:
                ok += 1
                geocoded += result.is_geocoded
                wrong_month += result.issued_at_utc.month != expected_month

    total = ok + sum(rejects.values())
    print(f"\n{month}: {ok}/{total} clean ({ok / total:.2%}), {geocoded / ok:.1%} geocoded")
    print(f"  dated outside the month (UTC): {wrong_month}")
    for (field, reason), count in rejects.most_common():
        print(f"  reject {field}: {reason} x{count}  e.g. {examples[field]!r}")