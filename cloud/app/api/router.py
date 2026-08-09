"""Aggregate API router."""
from fastapi import APIRouter

from app.api.routes import agents, auth, health, integrations, notifications, sites
from app.api.routes import map as map_routes

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(sites.router)
api_router.include_router(integrations.router)
api_router.include_router(auth.router)
api_router.include_router(map_routes.router)
api_router.include_router(agents.router)
api_router.include_router(notifications.router)
