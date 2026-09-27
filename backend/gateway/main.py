from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from gateway.db import SessionLocal, init_database
from gateway.http import router
from gateway.platform_http import router as platform_router
from gateway.seed import seed
from gateway.surface import router as surface_router
from gateway.settings import FRONTEND_DIST, load_dotenv

load_dotenv()
init_database()
with SessionLocal() as session:
    seed(session)

app = FastAPI(title="Bollard", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(surface_router)
app.include_router(platform_router)


@app.exception_handler(HTTPException)
async def http_error(_request, exc: HTTPException):
    from fastapi.responses import JSONResponse

    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    headers = exc.headers or None
    return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=headers)


if FRONTEND_DIST.is_dir():
    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}")
    def console(path: str):
        if path.startswith(("api/", "v1/", "mcp/", "auth/")) or path in {"health", "metrics", "api", "v1", "auth"}:
            raise HTTPException(status_code=404, detail={"message": "Not found."})
        target = (FRONTEND_DIST / path).resolve()
        if target.is_file() and FRONTEND_DIST.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
