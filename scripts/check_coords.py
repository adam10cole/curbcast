# check how often there's unclean data between the 2016 and 2026 dataset 
# super small ~1% so it's better to just reject then accept them as null in 
# the normalized schema 

from collections import Counter
import json
from pathlib import Path
import re


for month in ("year=2016/month=09", "year=2026/month=09"):
    shapes: Counter[str] = Counter()
    no_coords = total = 0
    for page in sorted(Path(f"data/raw/{month}").glob("page_*.json")):
        for feature in json.loads(page.read_text())["features"]:
            attrs = feature["attributes"]
            t = attrs.get("ISSUE_TIME")
            shapes["<None>" if t is None else re.sub(r"\d", "9", str(t))] +=1
            no_coords += attrs.get("LATITUDE") is None
            total += 1
    print(month, f"{total} rows, {no_coords / total:.1%} without coordinates")
    for shape, count in shapes.most_common(10):
        print(f"{shape!r:12} {count}")