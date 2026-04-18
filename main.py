from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.infrastructure.database.connection import init_db
from app.presentation.routers import webhook, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Stok Manager API", version="1.0.0", lifespan=lifespan)

app.include_router(webhook.router)
app.include_router(health.router)
