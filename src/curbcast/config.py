import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ARCGIS_BASE_URL = "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    arcgis_base_url: str
    database_url: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "data")),
            arcgis_base_url=os.environ.get("ARCGIS_BASE_URL", DEFAULT_ARCGIS_BASE_URL),
            database_url=os.environ.get("DATABASE_URL"),
        )

    def require_database_url(self) -> str:
        if not self.database_url:
            raise SystemExit("DATABASE_URL is not set or not running with .env file")
        return self.database_url
