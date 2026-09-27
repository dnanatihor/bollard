import os
from pathlib import Path
from urllib.parse import quote_plus

PACKAGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PACKAGE_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
DATA_DIR = REPO_ROOT / "data"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


def database_url() -> str:
    configured = os.environ.get("GATEWAY_DATABASE_URL", "").strip()
    if configured:
        return configured
    host = os.environ.get("POSTGRES_HOST", "").strip()
    if host:
        user = quote_plus(os.environ.get("POSTGRES_USER", "gateway"))
        password = os.environ.get("POSTGRES_PASSWORD", "").strip()
        if not password:
            raise RuntimeError("POSTGRES_PASSWORD is required when POSTGRES_HOST is set.")
        password = quote_plus(password)
        name = quote_plus(os.environ.get("POSTGRES_DB", "gateway"))
        port = os.environ.get("POSTGRES_PORT", "5432").strip()
        if not port.isdigit():
            port = "5432"
        return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DATA_DIR / 'gateway.db'}"


def load_dotenv() -> None:
    path = REPO_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value
