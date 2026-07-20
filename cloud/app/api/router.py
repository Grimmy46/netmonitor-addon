"""Aggregate API router."""
from fastapi import APIRouter

from app.api.routes import health, integrations, sites

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(sites.router)
api_router.include_router(integrations.router)
