import os

import uvicorn

from gateway.settings import BACKEND_DIR


def main() -> None:
    port = int(os.environ.get("GATEWAY_PORT", "8080"))
    reload = os.environ.get("GATEWAY_RELOAD", "1") != "0"
    uvicorn.run(
        "gateway.main:app",
        host=os.environ.get("GATEWAY_HOST", "127.0.0.1"),
        port=port,
        reload=reload,
        reload_dirs=[str(BACKEND_DIR / "gateway"), str(BACKEND_DIR / "alembic")] if reload else None,
    )
