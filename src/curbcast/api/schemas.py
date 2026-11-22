"""Shapes of API responses. FastAPI uses these to validate output and to document the API."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

PATROL_NOTE = (
    "Chances cover patrol enforcement only (meters, time limits, permits, tags). "
    "Always check posted signs."
)


class Block(BaseModel):
    lat: float = Field(description="Latitude of the matched street segment's midpoint")
    lon: float = Field(description="Longitude of the matched street segment's midpoint")
    distance_m: float = Field(description="Distance from the requested point, in metres")


class RestrictionWarningOut(BaseModel):
    kind: str = Field(description="rush_hour_am, rush_hour_pm, street_cleaning, ...")
    message: str
    start: datetime
    end: datetime


class HourOut(BaseModel):
    start: datetime
    end: datetime
    chance: float = Field(description="Chance of a ticket during this part of the stay, 0–1")
    holiday: bool


class RiskResponse(BaseModel):
    """A forecast. Warnings come first: a tow-away restriction matters more than any chance."""

    status: Literal["ok"] = "ok"
    warnings: list[RestrictionWarningOut]
    chance: float = Field(description="Chance of a ticket at some point during the stay, 0–1")
    hours: list[HourOut]
    block: Block
    run_id: int
    trained_through: date
    note: str = PATROL_NOTE


class NoDataResponse(BaseModel):
    """No trustworthy answer for this point. Never treat this as 0%."""

    status: Literal["not_enough_data"] = "not_enough_data"
    reason: str
    distance_m: float | None = None


class ActiveRun(BaseModel):
    run_id: int
    trained_through: date


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    active_run: ActiveRun | None
