"""CurbCast HTTP API.

Run locally from the repo root:
    uv run --env-file .env uvicorn curbcast.api.app:app --reload

Interactive documentation is served at /docs.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from psycopg import Connection
from psycopg_pool import ConnectionPool

from curbcast.api.schemas import (
    ActiveRun,
    Block,
    HealthResponse,
    HourOut,
    NoDataResponse,
    RestrictionWarningOut,
    RiskResponse,
)
from curbcast.config import Settings
from curbcast.model.predict import NotEnoughData, PredictError, predict

from fastapi import Response
from fastapi.middleware.gzip import GZipMiddleware

from curbcast.model.predict import risk_map
from pathlib import Path

from fastapi.staticfiles import StaticFiles

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open a small pool of database connections at startup; close it at shutdown."""
    settings = Settings.from_env()
    pool = ConnectionPool(settings.require_database_url(), min_size=1, max_size=5, open=True)
    app.state.pool = pool
    try:
        yield
    finally:
        pool.close()


app = FastAPI(
    title="CurbCast API",
    version="0.1.0",
    description="Chance of a parking ticket on a DC block, from four years of city ticket data.",
    lifespan=lifespan,
)


def get_conn(request: Request) -> Iterator[Connection[Any]]:
    """Borrow a connection from the pool for the duration of one request."""
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:
        yield conn


Conn = Annotated[Connection[Any], Depends(get_conn)]


@app.get("/health", response_model=HealthResponse)
def health(conn: Conn) -> HealthResponse:
    """The database is reachable, and which model is active (if any)."""
    row = conn.execute("SELECT id, cutoff_date FROM model_runs WHERE is_active").fetchone()
    active = None if row is None else ActiveRun(run_id=int(row[0]), trained_through=row[1])
    return HealthResponse(active_run=active)


@app.get("/v1/risk", response_model=RiskResponse | NoDataResponse)
def risk(
    conn: Conn,
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude of where you'll park")],
    lon: Annotated[float, Query(ge=-180, le=180, description="Longitude of where you'll park")],
    start: Annotated[
        datetime,
        Query(description="Start of the stay, ISO 8601. Without a timezone it's DC local time."),
    ],
    minutes: Annotated[int, Query(ge=1, le=1440, description="Length of the stay")] = 60,
) -> RiskResponse | NoDataResponse:
    """Chance of a ticket on the nearest block during the stay, plus any posted restrictions."""
    try:
        result = predict(conn, lat, lon, start, timedelta(minutes=minutes))
    except PredictError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if isinstance(result, NotEnoughData):
        return NoDataResponse(reason=result.reason, distance_m=result.distance_m)

    return RiskResponse(
        warnings=[
            RestrictionWarningOut(kind=w.kind, message=w.message, start=w.start, end=w.end)
            for w in result.warnings
        ],
        chance=result.combined,
        hours=[
            HourOut(start=h.start, end=h.end, chance=h.p, holiday=h.holiday)
            for h in result.hours
        ],
        block=Block(
            lat=result.seg_lat, lon=result.seg_lon, distance_m=result.distance
        ),
        run_id=result.run_id,
        trained_through=result.model_cutoff,
    )

app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.get("/v1/map")
def map_snapshot(
    conn: Conn,
    response: Response,
    at: Annotated[
        datetime,
        Query(description="Moment to map, ISO 8601. Without a timezone it's DC local time."),
    ],
) -> dict[str, Any]:
    """Every scored block's chance of a ticket in that hour, as GeoJSON points."""
    try:
        snapshot = risk_map(conn, at)
    except PredictError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    response.headers["Cache-Control"] = "public, max-age=3600"
    return {
        "type": "FeatureCollection",
        "run_id": snapshot.run_id,
        "trained_through": snapshot.trained_through.isoformat(),
        "at": snapshot.at.isoformat(),
        "holiday": snapshot.holiday,
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(b.lon, 6), round(b.lat, 6)]},
                "properties": {
                    "id": b.segment_id,
                    "chance": round(b.chance, 4),
                    "warnings": list(b.warnings),
                },
            }
            for b in snapshot.blocks
        ],
    }

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")