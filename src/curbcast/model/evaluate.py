import json
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from curbcast.model.fit import day_weights, segment_rates, slot_counts, weekday_totals
from curbcast.model.params import ModelParams

SLOT_KEYS = ["x", "y", "weekday", "hour"]
CAPTURE_FRACTIONS = (0.01, 0.02, 0.05)
BAND_EDGES = [0.0, 0.001, 0.01, 0.05, 0.10, 0.20, 0.40, 1.0]
BAND_LABELS = ["<0.1%", "0.1–1%", "1–5%", "5–10%", "10–20%", "20–40%", "40%+"]


class EvaluationError(Exception): ...


@dataclass(frozen=True)
class Evaluation:
    test_start: date
    test_end: date
    n_slots: int
    test_hits: int
    test_hits_on_grid: int
    log_likelihood: float
    null_log_likelihood: float
    place_only_log_likelihood: float
    time_only_log_likelihood: float
    capture: dict[float, float]
    calibration: pd.DataFrame

    @staticmethod
    def _improvement(model: float, reference: float) -> float:
        return 1 - model / reference

    @property
    def vs_null(self) -> float:
        return self._improvement(self.log_likelihood, self.null_log_likelihood)

    @property
    def vs_place_only(self) -> float:
        return self._improvement(self.log_likelihood, self.place_only_log_likelihood)

    @property
    def vs_time_only(self) -> float:
        return self._improvement(self.log_likelihood, self.time_only_log_likelihood)

    def to_json(self) -> dict[str, object]:
        """Plain-JSON form, stored with each fitted model in model_runs."""
        return {
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "n_slots": self.n_slots,
            "test_hits": self.test_hits,
            "test_hits_on_grid": self.test_hits_on_grid,
            "log_likelihood": self.log_likelihood,
            "null_log_likelihood": self.null_log_likelihood,
            "place_only_log_likelihood": self.place_only_log_likelihood,
            "time_only_log_likelihood": self.time_only_log_likelihood,
            "vs_null": self.vs_null,
            "vs_place_only": self.vs_place_only,
            "vs_time_only": self.vs_time_only,
            "capture": {f"{fraction:.2f}": share for fraction, share in self.capture.items()},
            "calibration": json.loads(self.calibration.to_json(orient="records")),
        }


def outcome_counts(
    test_hits: pd.DataFrame, start: date, end: date, holiday_dates: frozenset[date]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range(start, end, freq="D").as_unit("ns")
    holiday_index = pd.DatetimeIndex(sorted(holiday_dates)).as_unit("ns")
    is_holiday = dates.isin(holiday_index)

    calendar = pd.DataFrame({"weekday": dates.day_of_week, "is_holiday": is_holiday})
    days = (
        calendar.groupby("weekday")["is_holiday"]
        .agg(n_hol="sum", n_all="count")
        .reindex(range(7), fill_value=0)
        .rename_axis("weekday")
        .reset_index()
    )
    days["n_ord"] = days["n_all"] - days["n_hol"]
    days = days[["weekday", "n_ord", "n_hol"]].astype(np.int64)

    hit_dates = _as_ns(test_hits["local_date"])
    in_window = hit_dates.between(pd.Timestamp(start), pd.Timestamp(end))
    hits = test_hits.loc[in_window].assign(
        weekday=test_hits.loc[in_window, "weekday"].astype(np.int64),
        hour=test_hits.loc[in_window, "hour"].astype(np.int64),
        is_holiday=hit_dates[in_window].isin(holiday_index).to_numpy(),
    )
    hits["hol"] = hits["is_holiday"].astype(np.int64)
    hits["ord"] = 1 - hits["hol"]
    slots = hits.groupby(SLOT_KEYS, as_index=False).agg(k_ord=("ord", "sum"), k_hol=("hol", "sum"))
    return slots, days


def _attach_outcomes(
    predictions: pd.DataFrame, slots: pd.DataFrame, days: pd.DataFrame
) -> pd.DataFrame:
    grid = predictions.assign(
        weekday=predictions["weekday"].astype(np.int64),
        hour=predictions["hour"].astype(np.int64),
    )
    grid = grid.merge(slots, on=SLOT_KEYS, how="left").fillna({"k_ord": 0, "k_hol": 0})
    return grid.merge(days, on="weekday")


def log_likelihood(scored: pd.DataFrame, p: pd.Series, holiday_factor: float) -> float:
    if not p.between(0, 1, inclusive="neither").all():
        raise EvaluationError("probabilities must be strictly between 0 and 1")
    p_hol = np.minimum(p * holiday_factor, 0.99)

    total = (
        scored["k_ord"] * np.log(p)
        + (scored["n_ord"] - scored["k_ord"]) * np.log1p(-p)
        + scored["k_hol"] * np.log(p_hol)
        + (scored["n_hol"] - scored["k_hol"]) * np.log1p(-p_hol)
    ).sum()
    return float(total / (scored["n_ord"] + scored["n_hol"]).sum())


def capture(scored: pd.DataFrame, total_test_hits: int) -> dict[float, float]:
    hits = (scored["k_ord"] + scored["k_hol"]).to_numpy()
    order = np.argsort(-scored["p"].to_numpy(), kind="stable")
    cumulative = np.cumsum(hits[order])
    return {
        fraction: float(cumulative[round(fraction * len(order)) - 1] / total_test_hits)
        for fraction in CAPTURE_FRACTIONS
    }


def calibration_bands(scored: pd.DataFrame) -> pd.DataFrame:
    banded = scored.assign(
        band=pd.cut(scored["p"], bins=BAND_EDGES, labels=BAND_LABELS, right=False),
        p_days=scored["p"] * scored["n_ord"],
    )
    table = banded.groupby("band", observed=True).agg(
        slots=("p", "size"),
        p_days=("p_days", "sum"),
        n_ord=("n_ord", "sum"),
        k_ord=("k_ord", "sum"),
    )
    table["predicted"] = table["p_days"] / table["n_ord"]
    table["actual"] = table["k_ord"] / table["n_ord"]
    return table[["slots", "predicted", "actual"]].reset_index().astype({"band": str})


def evaluate(
    predictions: pd.DataFrame,
    holiday_factor: float,
    train_hits: pd.DataFrame,
    train_start: date,
    train_end: date,
    test_hits: pd.DataFrame,
    test_start: date,
    test_end: date,
    test_holidays: frozenset[date],
    params: ModelParams,
) -> Evaluation:
    slots, days = outcome_counts(test_hits, test_start, test_end, test_holidays)
    scored = _attach_outcomes(predictions, slots, days)
    total_test_hits = int((slots["k_ord"] + slots["k_hol"]).sum())
    if total_test_hits == 0:
        raise EvaluationError(f"no test hits between {test_start} and {test_end}")

    null, place, time = _reference_probabilities(scored, train_hits, train_start, train_end, params)

    return Evaluation(
        test_start=test_start,
        test_end=test_end,
        n_slots=len(scored),
        test_hits=total_test_hits,
        test_hits_on_grid=int((scored["k_ord"] + scored["k_hol"]).sum()),
        log_likelihood=log_likelihood(scored, scored["p"], holiday_factor),
        null_log_likelihood=log_likelihood(scored, null, 1.0),
        place_only_log_likelihood=log_likelihood(scored, place, holiday_factor),
        time_only_log_likelihood=log_likelihood(scored, time, holiday_factor),
        capture=capture(scored, total_test_hits),
        calibration=calibration_bands(scored),
    )


def _reference_probabilities(
    scored: pd.DataFrame,
    train_hits: pd.DataFrame,
    train_start: date,
    train_end: date,
    params: ModelParams,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    n_segments = scored[["x", "y"]].drop_duplicates().shape[0]

    train_dates = pd.date_range(train_start, train_end, freq="D").as_unit("ns")
    train_dates = train_dates[~train_dates.strftime("%Y-%m").isin(sorted(params.excluded_months))]
    in_training = _as_ns(train_hits["local_date"]).isin(train_dates)
    null_p = int(in_training.sum()) / (n_segments * 24 * len(train_dates))
    null = pd.Series(null_p, index=scored.index)

    days = day_weights(train_start, train_end, params.half_life, params.excluded_months)
    totals = weekday_totals(days)
    counts = slot_counts(train_hits, days)

    rates = segment_rates(counts, totals)
    place = scored[["x", "y"]].merge(rates, on=["x", "y"], how="left")["seg_p"]

    by_time = counts.groupby(["weekday", "hour"], as_index=False).agg(k=("k", "sum"))
    by_time = by_time.merge(totals, on="weekday")
    by_time["p"] = by_time["k"] / (by_time["n"] * n_segments)
    time = scored[["weekday", "hour"]].merge(by_time, on=["weekday", "hour"], how="left")["p"]

    place.index = scored.index
    time.index = scored.index
    if place.isna().any() or time.isna().any():
        raise EvaluationError("reference models don't cover every grid slot")
    return null, place, time


def _as_ns(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values).dt.as_unit("ns")
