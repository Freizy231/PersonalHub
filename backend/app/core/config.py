from dataclasses import dataclass
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    data_root: Path
    legacy_database: Path
    reminders_enabled: bool = True
    port: int = 8765
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist"

    def __post_init__(self):
        if not 1024 <= self.port <= 65535:
            raise ValueError("PORT_INVALID")
        if self.data_root.is_symlink():
            raise ValueError("DATA_ROOT_SYMLINK")

    @property
    def database(self) -> Path:
        return self.data_root / "db" / "hub.db"

    @property
    def origins(self) -> frozenset[str]:
        return frozenset({f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"})

    @classmethod
    def from_environment(cls):
        # Separate namespace: never load, overwrite or reinterpret legacy .env.
        return cls(
            data_root=Path(os.environ.get("PERSONAL_HUB_DATA_ROOT", str(PROJECT_ROOT / "runtime" / "personal-hub"))).expanduser().absolute(),
            legacy_database=Path(os.environ.get("PERSONAL_HUB_LEGACY_DB", str(PROJECT_ROOT / "data" / "intel.db"))).expanduser().absolute(),
            reminders_enabled=os.environ.get("PERSONAL_HUB_REMINDERS", "1") != "0",
            port=int(os.environ.get("PERSONAL_HUB_PORT", "8765")),
        )
