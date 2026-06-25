from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

from backend.routers.analyze import router as analyze_router
from backend.routers.health import router as health_router
from backend.routers.ingest import router as ingest_router
from backend.routers.oauth import router as oauth_router
from backend.routers.profile import router as profile_router
from db.session import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.startup_error = None
    try:
        await create_tables()
    except Exception as exc:  # pragma: no cover - defensive startup guard
        app.state.startup_error = str(exc)
    yield


app = FastAPI(title="ShadowSelf", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(ingest_router)
app.include_router(oauth_router)
app.include_router(analyze_router)
app.include_router(profile_router)

static_dir = Path(__file__).parent / "frontend_static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


def main() -> None:
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
