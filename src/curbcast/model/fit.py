from datetime import date

import numpy as np
import pandas as pd

from curbcast.model.params import ModelParams


class FitError(Exception): ...


SLOT_KEYS = ["x", "y", "weekday", "hour"]
AREA_KEYS = ["ax", "ay"]
HOURS_PER_DAY = 24
HOURS_PER_WEEK = HOURS_PER_DAY * 7


# day weights
# w_d = 0.5^((T − d) / half life)
def day_weights(
    start: date, cutoff: date, half_life: float, excluded_months: frozenset[str]
) -> pd.DataFrame:
    dates = pd.date_range(start, cutoff, freq="D")
    dates = dates[~dates.strftime("%Y-%m").isin(sorted(excluded_months))]
    age_days = (pd.Timestamp(cutoff) - dates).days.to_numpy()
    return pd.DataFrame(
        {"local_date": dates, "weekday": dates.day_of_week, "weight": 0.5 ** (age_days / half_life)}
    )


# total day weight per weekday
# N[wd]
def weekday_totals(days: pd.DataFrame) -> pd.DataFrame:
    totals = days.groupby("weekday", as_index=False).agg(n=("weight", "sum"))
    totals = totals.rename(columns={"weight": "n"})
    if len(totals) != 7:
        raise FitError("expected 7 day training windows")
    return totals


# summed day weights of hits per slot
# (hits on a date outside the window/excluded month dont contribute)
def slot_counts(hits: pd.DataFrame, days: pd.DataFrame) -> pd.DataFrame:
    dated = hits.assign(local_date=_as_dates(hits["local_date"]))
    joined = dated.merge(days, on="local_date", how="inner", suffixes=("_hit", "_day"))

    disagreed = joined["weekday_hit"].astype(np.int64) != joined["weekday_day"]
    if disagreed.any():
        raise FitError(f"there are {int(disagreed.sum())} hits that a day disagrees w/ their date")

    joined = joined.rename(columns={"weekday_day": "weekday"})
    return joined.groupby(SLOT_KEYS, as_index=False).agg(k=("weight", "sum"))


# calculate segment rates
def segment_rates(counts: pd.DataFrame, totals: pd.DataFrame) -> pd.DataFrame:
    slot_days = HOURS_PER_DAY * totals["n"].sum()
    rates = counts.groupby(["x", "y"], as_index=False).agg(k=("k", "sum"))
    rates["seg_p"] = rates["k"] / slot_days
    return rates[["x", "y", "seg_p"]]


def city_distribution(counts: pd.DataFrame, totals: pd.DataFrame) -> pd.DataFrame:
    by_slot = counts.groupby(["weekday", "hour"], as_index=False)["k"].sum()
    city = _weekday_hours().merge(by_slot, on=["weekday", "hour"], how="left")
    city = city.fillna({"k": 0.0}).merge(totals, on="weekday")
    city["r"] = city["k"] / city["n"]

    total_r = city["r"].sum()
    if total_r <= 0:
        raise FitError("no hits in the training window")
    city["pi_city"] = city["r"] / total_r
    return city[["weekday", "hour", "pi_city"]]


def area_shapes(
    counts: pd.DataFrame,
    totals: pd.DataFrame,
    city: pd.DataFrame,
    cell_m: float,
    shrinkage: float,
) -> pd.DataFrame:
    keyed = _area_keys(counts, cell_m)
    area_slot = keyed.groupby([*AREA_KEYS, "weekday", "hour"], as_index=False).agg(k=("k", "sum"))

    areas = area_slot[AREA_KEYS].drop_duplicates()
    full = areas.merge(city, how="cross")
    full = full.merge(area_slot, on=[*AREA_KEYS, "weekday", "hour"], how="left")
    full = full.fillna({"k": 0.0}).merge(totals, on="weekday")

    full["r"] = full["k"] / full["n"]
    full["pi_a"] = full["r"] / full.groupby(AREA_KEYS)["r"].transform("sum")
    full["t_a"] = full.groupby(AREA_KEYS)["k"].transform("sum")
    full["shape"] = (
        HOURS_PER_WEEK
        * (full["t_a"] * full["pi_a"] + shrinkage * full["pi_city"])
        / (full["t_a"] + shrinkage)
    )
    return full[[*AREA_KEYS, "weekday", "hour", "shape"]]


def slot_probabilities(
    counts: pd.DataFrame,
    totals: pd.DataFrame,
    rates: pd.DataFrame,
    shapes: pd.DataFrame,
    params: ModelParams,
) -> pd.DataFrame:
    grid = rates.merge(_weekday_hours(), how="cross")
    grid = grid.merge(counts, on=SLOT_KEYS, how="left").fillna({"k": 0.0})
    grid = _area_keys(grid, params.region_size)
    grid = grid.merge(shapes, on=[*AREA_KEYS, "weekday", "hour"], how="left")
    grid = grid.merge(totals, on="weekday")

    if grid["shape"].isna().any():
        raise FitError("some segments have no area shape; area keys don't line up")

    grid["prior"] = np.minimum(grid["seg_p"] * grid["shape"], params.prior_cap)
    grid["p"] = (grid["k"] + params.m * grid["prior"]) / (grid["n"] + params.m)

    out_of_range = ~grid["p"].between(0, 1, inclusive="neither")
    if out_of_range.any():
        raise FitError(f"{int(out_of_range.sum())} slots have p outside (0, 1)")

    return grid[[*SLOT_KEYS, "p"]].sort_values(SLOT_KEYS, ignore_index=True)


def fit(hits: pd.DataFrame, start: date, cutoff: date, params: ModelParams) -> pd.DataFrame:
    if start > cutoff:
        raise FitError(f"start {start} is after {cutoff}")

    days = day_weights(start, cutoff, params.half_life, params.excluded_months)
    totals = weekday_totals(days)
    counts = slot_counts(hits, days)
    if counts.empty:
        raise FitError(f"no hits between {start} and {cutoff}")

    rates = segment_rates(counts, totals)
    city = city_distribution(counts, totals)
    shapes = area_shapes(counts, totals, city, params.region_size, params.c)

    return slot_probabilities(counts, totals, rates, shapes, params)


# Helpers
def _weekday_hours() -> pd.DataFrame:
    return pd.DataFrame(
        [(weekday, hour) for weekday in range(7) for hour in range(HOURS_PER_DAY)],
        columns=["weekday", "hour"],
    )


def _as_dates(column: pd.Series) -> pd.Series:
    return pd.to_datetime(column).astype("datetime64[ns]")


def _area_keys(frame: pd.DataFrame, m: float) -> pd.DataFrame:
    return frame.assign(
        ax=np.floor(frame["x"] / m).astype(np.int64),
        ay=np.floor(frame["y"] / m).astype(np.int64),
    )
