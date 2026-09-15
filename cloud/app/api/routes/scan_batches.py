"""Scan Station batches.

The Scan Station (/scan/index.html) is a static page on handhelds with no user
session, so these endpoints are guarded by a shared token rather than
`current_user`. The token is readable in the page source — it stops drive-by
posts from anything that stumbles on the URL, and nothing stronger. Keep that
in mind before putting anything sensitive in a batch.
"""
import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.models import ScanBatch

router = APIRouter(prefix="/scan-batches", tags=["scan-batches"])

MAX_ROWS = 5000
MAX_CSV_CHARS = 2_000_000


async def require_scan_token(
    x_scan_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    """Accept the token in a header, or a query param so a plain <a href> can
    pull a CSV without JavaScript."""
    expected = get_settings().scan_upload_token
    supplied = x_scan_token or token
    if not expected or supplied != expected:
        raise HTTPException(status_code=401, detail="Bad or missing scan token")


class ScanBatchIn(BaseModel):
    scanner: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)
    csv: str = ""
    rows: list[dict] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


class ScanBatchSummaryOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    scanner: str
    note: str
    row_count: int
    summary: dict
    imported_at: datetime | None


class ScanBatchOut(ScanBatchSummaryOut):
    csv: str
    rows: list[dict]


def _summarise(rows: list[dict]) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        key = str(r.get("category") or "unknown")
        out[key] = out.get(key, 0) + 1
    return out


@router.post("", status_code=201, dependencies=[Depends(require_scan_token)])
async def upload_batch(payload: ScanBatchIn, db: AsyncSession = Depends(get_db)) -> dict:
    rows = payload.rows
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=413, detail=f"Batch too large (>{MAX_ROWS} rows)")
    if len(payload.csv) > MAX_CSV_CHARS:
        raise HTTPException(status_code=413, detail="CSV too large")
    if not rows and not payload.csv.strip():
        raise HTTPException(status_code=400, detail="Empty batch")

    # Trust the CSV as the record, but derive the counts ourselves so a
    # mis-reporting client can't skew the viewer.
    if not rows and payload.csv:
        rows = list(csv.DictReader(io.StringIO(payload.csv)))

    batch = ScanBatch(
        scanner=payload.scanner.strip()[:120],
        note=payload.note.strip()[:500],
        row_count=len(rows),
        summary=payload.summary or _summarise(rows),
        csv_text=payload.csv,
        rows=rows,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return {"id": str(batch.id), "created_at": batch.created_at.isoformat(), "row_count": batch.row_count}


@router.get("", dependencies=[Depends(require_scan_token)])
async def list_batches(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ScanBatchSummaryOut]:
    res = await db.execute(select(ScanBatch).order_by(ScanBatch.created_at.desc()).limit(limit))
    return [
        ScanBatchSummaryOut(
            id=b.id, created_at=b.created_at, scanner=b.scanner, note=b.note,
            row_count=b.row_count, summary=b.summary or {}, imported_at=b.imported_at,
        )
        for b in res.scalars().all()
    ]


async def _get(db: AsyncSession, batch_id: uuid.UUID) -> ScanBatch:
    batch = await db.get(ScanBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="No such batch")
    return batch


@router.get("/{batch_id}", dependencies=[Depends(require_scan_token)])
async def get_batch(batch_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ScanBatchOut:
    b = await _get(db, batch_id)
    return ScanBatchOut(
        id=b.id, created_at=b.created_at, scanner=b.scanner, note=b.note,
        row_count=b.row_count, summary=b.summary or {}, imported_at=b.imported_at,
        csv=b.csv_text, rows=b.rows or [],
    )


@router.get("/{batch_id}/csv", dependencies=[Depends(require_scan_token)])
async def get_batch_csv(batch_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Response:
    b = await _get(db, batch_id)
    stamp = b.created_at.astimezone(timezone.utc).strftime("%Y%m%d-%H%M")
    who = "".join(c for c in (b.scanner or "scan") if c.isalnum() or c in "-_") or "scan"
    return Response(
        content=b.csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{who}-{stamp}.csv"'},
    )


@router.post("/{batch_id}/imported", dependencies=[Depends(require_scan_token)])
async def mark_imported(
    batch_id: uuid.UUID,
    undo: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> dict:
    b = await _get(db, batch_id)
    b.imported_at = None if undo else datetime.now(tz=timezone.utc)
    await db.commit()
    return {"id": str(b.id), "imported_at": b.imported_at.isoformat() if b.imported_at else None}


@router.delete("/{batch_id}", status_code=204, dependencies=[Depends(require_scan_token)])
async def delete_batch(batch_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Response:
    b = await _get(db, batch_id)
    await db.delete(b)
    await db.commit()
    return Response(status_code=204)
