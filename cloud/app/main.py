"""FastAPI application entrypoint."""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import get_settings
from app.workers.poller import poller_lifespan

settings = get_settings()

# App loggers (netmonitor.*) at INFO so alert sweeps / pushes show in docker logs.
_nm = logging.getLogger("netmonitor")
if not _nm.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _nm.addHandler(_h)
    _nm.setLevel(logging.INFO)
    _nm.propagate = False

app = FastAPI(
    title="NetMonitor Cloud",
    version=__version__,
    description="Multi-site network operations backend (UniFi Site Manager + local agents).",
    lifespan=poller_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {"service": "netmonitor-cloud", "version": __version__, "docs": "/docs"}
