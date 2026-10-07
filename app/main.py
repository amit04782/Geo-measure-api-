from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.database import Base, engine
from app.routers import files


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="Geospatial File Measurement API",
    description="Upload a zipped Shapefile or KML and get CRS-correct areas and lengths.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(files.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
