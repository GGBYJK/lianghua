from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .kline_schemas import KlineDatasetCreateRequest, KlineDatasetUpdateRequest
from .analysis_cache_store import analysis_cache_stats, clear_analysis_cache
from .kline_service import current_market_provider
from .kline_store import (
    KlineStoreError,
    create_kline_dataset,
    delete_kline_dataset,
    enqueue_all_kline_syncs,
    enqueue_kline_sync,
    list_kline_bars,
    list_kline_datasets,
    list_kline_sync_jobs,
    update_kline_dataset,
)
from .trading_api import require_permission


router = APIRouter(prefix="/api/admin/kline-data", tags=["kline-data"])


def _error(exc: KlineStoreError, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=str(exc))


@router.get("/datasets")
def datasets(_: dict[str, Any] = Depends(require_permission("market_data.manage"))) -> list[dict[str, Any]]:
    return list_kline_datasets()


@router.post("/datasets", status_code=status.HTTP_202_ACCEPTED)
def create_dataset(
    payload: KlineDatasetCreateRequest,
    user: dict[str, Any] = Depends(require_permission("market_data.manage")),
) -> dict[str, Any]:
    try:
        return create_kline_dataset(
            int(user["id"]),
            payload.symbol,
            payload.timeframe,
            current_market_provider(),
            payload.target_count,
            payload.auto_update,
        )
    except KlineStoreError as exc:
        raise _error(exc, 409) from exc


@router.patch("/datasets/{dataset_id}")
def update_dataset(
    dataset_id: str,
    payload: KlineDatasetUpdateRequest,
    _: dict[str, Any] = Depends(require_permission("market_data.manage")),
) -> dict[str, Any]:
    try:
        return update_kline_dataset(
            dataset_id,
            target_count=payload.target_count,
            auto_update=payload.auto_update,
        )
    except KlineStoreError as exc:
        raise _error(exc, 404) from exc


@router.delete("/datasets/{dataset_id}")
def remove_dataset(
    dataset_id: str,
    _: dict[str, Any] = Depends(require_permission("market_data.manage")),
) -> dict[str, bool]:
    try:
        delete_kline_dataset(dataset_id)
    except KlineStoreError as exc:
        raise _error(exc, 409 if "正在更新" in str(exc) else 404) from exc
    return {"ok": True}


@router.post("/datasets/{dataset_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def sync_dataset(
    dataset_id: str,
    _: dict[str, Any] = Depends(require_permission("market_data.manage")),
) -> dict[str, Any]:
    try:
        return enqueue_kline_sync(dataset_id)
    except KlineStoreError as exc:
        raise _error(exc, 404) from exc


@router.post("/sync-all", status_code=status.HTTP_202_ACCEPTED)
def sync_all(_: dict[str, Any] = Depends(require_permission("market_data.manage"))) -> dict[str, Any]:
    jobs = enqueue_all_kline_syncs()
    return {"queued": len(jobs), "jobs": jobs}


@router.get("/datasets/{dataset_id}/bars")
def bars(
    dataset_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
    _: dict[str, Any] = Depends(require_permission("market_data.manage")),
) -> dict[str, Any]:
    try:
        return list_kline_bars(dataset_id, page, page_size)
    except KlineStoreError as exc:
        raise _error(exc, 404) from exc


@router.get("/jobs")
def jobs(
    limit: int = Query(default=100, ge=1, le=500),
    _: dict[str, Any] = Depends(require_permission("market_data.manage")),
) -> list[dict[str, Any]]:
    return list_kline_sync_jobs(limit)


@router.get("/analysis-cache")
def cache_stats(
    _: dict[str, Any] = Depends(require_permission("market_data.manage")),
) -> dict[str, int]:
    return analysis_cache_stats()


@router.delete("/analysis-cache")
def clear_cache(
    _: dict[str, Any] = Depends(require_permission("market_data.manage")),
) -> dict[str, int]:
    return {"deleted": clear_analysis_cache()}
