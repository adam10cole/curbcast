from datetime import date

import holidays as holiday_calendar
import pandas as pd


class HolidayError(Exception): ...


def weekday_holidays(start: date, end: date) -> frozenset[date]:
    calendar = holiday_calendar.country_holidays(
        "US", subdiv="DC", years=range(start.year, end.year + 1), observed=True
    )
    return frozenset(day for day in calendar if start <= day <= end and day.weekday() < 5)


def estimate_holiday_factor(
    hits: pd.DataFrame,
    start: date,
    end: date,
    excluded_months: frozenset[str],
    holiday_dates: frozenset[str],
) -> float:
    dates = pd.date_range(start, end, freq="D").as_unit("ns")
    dates = dates[~dates.strftime("%Y-%m").isin(sorted(excluded_months))]

    hit_dates = pd.to_datetime(hits["local_date"]).dt.as_unit("ns")
    daily = hit_dates.value_counts().reindex(dates, fill_value=0)

    holiday_index = pd.DatetimeIndex(sorted(holiday_dates)).as_unit("ns")
    frame = pd.DataFrame(
        {
            "hits": daily.to_numpy(),
            "weekday": dates.day_of_week,
            "is_holiday": dates.isin(holiday_index),
        },
        index=dates,
    )

    on_holidays = frame[frame["is_holiday"]]
    if on_holidays.empty:
        raise HolidayError(f"no weekday holidays between {start} and {end}")

    mean = frame[~frame["is_holiday"]].groupby("weekday")["hits"].mean()
    expected = on_holidays["weekday"].map(mean)
    if expected.isna().any() or expected.sum() <= 0:
        raise HolidayError("no ordinary baseline for some holiday weekdays")

    factor = float(on_holidays["hits"].sum() / expected.sum())
    if not 0 < factor < 1:
        raise HolidayError(f"holiday factor {factor:.4f} is outside of 0 and 1")

    return factor


def holiday_probablity(p: float, factor: float) -> float:
    return min(p * factor, 0.99)
