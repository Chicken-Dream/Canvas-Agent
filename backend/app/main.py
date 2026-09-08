from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  (registers tables with Base.metadata)
from .config import settings
from .database import init_models
from .routers import agent, assignments, auth, courses, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    yield


app = FastAPI(title="Canvas Assignment Agent - Prototype API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(courses.router)
app.include_router(assignments.router)
app.include_router(sync.router)
app.include_router(agent.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
