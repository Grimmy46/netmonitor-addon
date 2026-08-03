"""Site-map endpoints: persist node positions (and, later, links + background)."""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.models import Site

router = APIRouter(prefix="/map", tags=["map"])


class Position(BaseModel):
    site_id: uuid.UUID
    x: float
    y: float


class PositionsIn(BaseModel):
    positions: list[Position]


@router.put("/positions", status_code=204)
async def save_positions(body: PositionsIn, db: AsyncSession = Depends(get_db)) -> None:
    """Save fleet-map node positions. Sent on drag-drop; idempotent upsert."""
    ids = [p.site_id for p in body.positions]
    if not ids:
        return
    rows = (await db.execute(select(Site).where(Site.id.in_(ids)))).scalars().all()
    by_id = {s.id: s for s in rows}
    for p in body.positions:
        site = by_id.get(p.site_id)
        if site is not None:
            site.map_x = p.x
            site.map_y = p.y
    await db.commit()
