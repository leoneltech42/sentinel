from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import follow, outcomes, picks, pnl, refresh

app = FastAPI(title="Sentinel API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(picks.router)
app.include_router(outcomes.router)
app.include_router(follow.router)
app.include_router(pnl.router)
app.include_router(refresh.router)
