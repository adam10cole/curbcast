import calendar
import hashlib
import json
import logging
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class ExtractError(Exception): ...  # base error class


class ArcGISError(
    ExtractError
): ...  # server returned HTTP 200 but has {"error"} in body


class CountMismatchError(ExtractError): ...  # fetched rows != expected rows


class MonthNotPublishedError(ExtractError): ...  # couldn't find layer for that month


class AmbiguousLayerError(ExtractError): ...  # more than one layer matched month


@dataclass(frozen=True)
class MonthRef:
    year: int
    month: int

    def __str__(self) -> str:
        return f"{self.year}-{self.month:02d}"


log = logging.getLogger(__name__)
TIMEOUT = (5, 30)  # connect, read (in seconds)
DEFAULT_PAGE_SIZE = 1000  # default for ArcGIS
DEFAULT_OID_FIELD = "OBJECTID"


def build_session() -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),  # tmp errors
        allowed_methods=("GET",),
        raise_on_status=False,
    )

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = (
        "curbcast-ingest/0.1 (+https://github.com/adam10cole/curbcast)"
    )
    return session


def get_json(session: requests.Session, url: str, params: dict | None = None) -> dict:
    params = {"f": "json", **(params or {})}
    log.debug("GET %s params=%s", url, params)

    response = session.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()

    try:
        body = response.json()
    except requests.JSONDecodeError as exc:
        raise ArcGISError(
            f"Non JSON response from {url}: {response.text[:200]!r}"
        ) from exc

    if "error" in body:
        err = body["error"]
        raise ArcGISError(
            f"ArcGIS error {err.get('code')} from {url}: {err.get('message')}"
        )

    return body


def find_layer_id(session: requests.Session, service_url: str, ref: MonthRef) -> int:
    # Seems to always be month - 1, but any given year could change format
    layers = get_json(session, service_url).get("layers", [])

    month = calendar.month_name[ref.month].lower()
    year = str(ref.year)

    matches = [
        layer
        for layer in layers
        if month in _normalize(layer["name"]) and year in _normalize(layer["name"])
    ]

    if not matches:
        raise MonthNotPublishedError(
            f"No layer for {ref} in {service_url} {[layer['name'] for layer in layers]}"
        )
    if len(matches) > 1:
        raise AmbiguousLayerError(
            f"{len(matches)} layers match {ref.year}-{ref.month:02d} "
            f"{[layer['name'] for layer in matches]}"
        )

    return matches[0]["id"]


def fetch_layer_metadata(session: requests.Session, layer_url: str) -> dict:
    return get_json(session, layer_url)


def service_url_for(base_url: str, year: int) -> str:
    return f"{base_url.rstrip('/')}/Violations_Parking_{year}/MapServer"


def fetch_count(session: requests.Session, layer_url: str) -> int:
    body = get_json(
        session,
        f"{layer_url}/query",
        {"where": "1=1", "returnCountOnly": "true"},
    )

    try:
        return int(body["count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ArcGISError(f"Error in the response from {layer_url}: {body!r}") from exc


def iter_pages(
    session: requests.Session,
    layer_url: str,
    oid_field: str,
    page_size: int,
) -> Iterator[dict]:
    last_seen = -1
    while True:
        payload = get_json(
            session,
            f"{layer_url}/query",
            {
                "where": f"{oid_field} > {last_seen}",
                "outFields": "*",
                "orderByFields": f"{oid_field} ASC",
                "resultRecordCount": page_size,
            },
        )
        features = payload.get("features", [])
        if not features:
            return

        page_max = max(feature["attributes"][oid_field] for feature in features)
        if page_max <= last_seen:
            raise ArcGISError(
                f"Paging stalled at {oid_field}={last_seen} on {layer_url}"
            )

        yield payload
        last_seen = page_max


def write_page(out_dir: Path, page_num: int, payload: dict) -> str:
    return _write_json(out_dir / f"page_{page_num:04d}.json", payload)


def extract_month(
    session: requests.Session,
    base_url: str,
    ref: MonthRef,
    data_dir: Path,
) -> Path:
    service_url = service_url_for(base_url, ref.year)
    layer_id = find_layer_id(session, service_url, ref)
    layer_url = f"{service_url}/{layer_id}"
    log.info("Starting extraction %s from %s", ref, layer_url)

    final_dir = data_dir / "raw" / f"year={ref.year}" / f"month={ref.month:02d}"
    work_dir = final_dir.with_name(final_dir.name + ".partial")
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    metadata = fetch_layer_metadata(session, layer_url)
    _write_json(work_dir / "layer_metadata.json", metadata)
    oid_field = _oid_field(metadata)
    page_size = int(metadata.get("maxRecordCount", DEFAULT_PAGE_SIZE))

    expected = fetch_count(session, layer_url)
    pages: list[dict] = []
    fetched = 0

    for page_num, payload in enumerate(
        iter_pages(session, layer_url, oid_field, page_size), start=1
    ):
        rows = len(payload["features"])
        sha256 = write_page(work_dir, page_num, payload)
        pages.append(
            {"file": f"page_{page_num:04d}.json", "rows": rows, "sha256": sha256}
        )
        fetched += rows
        log.debug("%s page %d: %d rows (%d/%d)", ref, page_num, rows, fetched, expected)

    if fetched != expected:
        raise CountMismatchError(
            f"{ref}: server reported {expected} rows but fetched {fetched}"
        )

    manifest = {
        "month": str(ref),
        "source_url": layer_url,
        "layer_id": layer_id,
        "layer_name": metadata.get("name"),
        "fetched_at": datetime.now(UTC).isoformat(),
        "fetched_count": fetched,
        "expected_count": expected,
        "pages": pages,
    }

    _write_json(work_dir / "manifest.json", manifest)

    if final_dir.exists():
        shutil.rmtree(final_dir)
    work_dir.rename(final_dir)

    log.info("Extracted %s: %d rows in %d pages", ref, fetched, len(pages))
    return final_dir


### Helpers
def _normalize(text: str) -> str:
    return text.replace("_", " ").lower()


def _oid_field(metadata: dict) -> str:
    for field in metadata.get("fields", []):
        if field.get("type") == "esriFieldTypeOID":
            return field["name"]
    return DEFAULT_OID_FIELD


def _write_json(path: Path, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()
