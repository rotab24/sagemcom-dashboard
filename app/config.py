import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.environ.get("ENV_FILE", ROOT / ".env"))
load_dotenv(ENV_FILE)


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "si", "sí")


@dataclass(frozen=True)
class Settings:
    host: str = os.environ.get("SAGEMCOM_HOST", "192.168.1.1")
    user: str = os.environ.get("SAGEMCOM_USER", "admin")
    password: str = os.environ.get("SAGEMCOM_PASS", "")
    scheme: str = os.environ.get("SAGEMCOM_SCHEME", "http")
    mock: bool = _bool("SAGEMCOM_MOCK")
    # Cada cuántos segundos se guarda una muestra de tráfico/fibra en el histórico
    collect_interval: int = int(os.environ.get("COLLECT_INTERVAL", "60"))
    # Segundos que se reutiliza la lista de dispositivos: leerla cuesta al router ~10 s
    # y la piden a la vez el navegador y el recolector
    cache_ttl: int = int(os.environ.get("CACHE_TTL", "50"))
    db_path: Path = Path(os.environ.get("DB_PATH", ROOT / "data" / "history.sqlite3"))
    automations_file: Path = Path(os.environ.get("AUTOMATIONS_FILE", ROOT / "automations.json"))


settings = Settings()
