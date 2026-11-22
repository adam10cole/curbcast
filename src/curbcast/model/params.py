import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelParams:
    half_life: float = 180.0  # recency (in days)
    region_size: float = 500.0  # how big each local pattern square is (meters)
    c: float = 300.0  # blended weighted hits of city pattern
    m: float = 50.0  # blended weighted days of prior pseudo data
    prior_cap: float = 0.99  # keeps every probabilty below 1
    target_categories: frozenset[str] = frozenset({"patrol", "vehicle", "location"})
    excluded_months: frozenset[str] = frozenset({"2025-08"})
    location_source: str = "xy"

    def __post_init__(self) -> None:
        for name in ("half_life", "region_size", "c", "m"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} has to be positive not {getattr(self, name)}")
        if not 0 < self.prior_cap < 1:
            raise ValueError(f"prior cap has to between 0 and 1 not {self.prior_cap}")

    def to_json(self) -> dict[str, object]:
        return {
            key: sorted(value) if isinstance(value, frozenset) else value
            for key, value in dataclasses.asdict(self).items()
        }
