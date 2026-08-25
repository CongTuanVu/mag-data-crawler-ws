"""API dữ liệu ws1 — FastAPI trước DuckDB.

Chạy sau nginx: `/ws1-data/api/` được proxy tới đây và tiền tố bị cắt, nên service
phục vụ ở gốc (`/health`, `/markets`, …).

Hai chế độ, chọn bằng biến `DATA_MODE`:

    mock  (mặc định)  — dữ liệu giả trong `mock.py`, để dựng và kiểm hạ tầng
    real              — đọc thật parquet qua DuckDB (`queries.py`, `vn.py`)

Hình dạng phản hồi giống hệt nhau ở cả hai chế độ; đổi chế độ là đổi một biến môi
trường, không đổi frontend. Mọi phản hồi mang cờ `mock` và header `X-Data-Mode`
để không ai nhìn số giả mà tưởng số thật.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import config, mock

MODE = os.environ.get("DATA_MODE", "mock").strip().lower()
IS_MOCK = MODE != "real"

if not IS_MOCK:                       # nạp muộn: chế độ mock không cần duckdb chạy
    from . import queries, vn

app = FastAPI(
    title="ws1 data API",
    version="0.1.0",
    description="Truy cập corpus toà nhà, bốn bảng Việt Nam và bộ tài liệu.",
    docs_url="/docs", openapi_url="/openapi.json",
)


@app.middleware("http")
async def stamp_mode(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Data-Mode"] = "mock" if IS_MOCK else "real"
    return resp


def check_slug(kind: str, value: str | None, table: dict[str, str]) -> None:
    """Slug không tra được thì BÁO LỖI, đừng lặng lẽ bỏ bộ lọc.

    Bỏ qua trong im lặng nghĩa là người dùng hỏi Hà Nội, nhận về cả kho, mà không
    có dấu hiệu nào cho biết bộ lọc đã rơi mất.
    """
    if not value:
        return
    from .slugs import resolve
    if resolve(table, value) is None:
        near = sorted(k for k in table if k.startswith(value[:3].lower()))[:5]
        raise HTTPException(422, {
            "error": f"{kind} '{value}' không có trong dữ liệu",
            "hint": f"dùng slug từ /vn/{'provinces' if kind == 'province' else 'categories'}",
            "did_you_mean": near or sorted(table)[:5],
        })


def ok(payload: Any) -> JSONResponse:
    if isinstance(payload, dict):
        payload = {**payload, "mock": IS_MOCK}
    else:
        payload = {"rows": payload, "mock": IS_MOCK}
    return JSONResponse(payload)


# ── hạ tầng ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """nginx và docker healthcheck gọi đường này."""
    out = {"status": "ok", "mode": "mock" if IS_MOCK else "real",
           "version": app.version}
    if not IS_MOCK:
        from . import db
        try:
            out["corpus_rows"] = db.scalar(
                f"select count(*) from read_parquet('{config.corpus('corpus_loose')}')")
        except Exception as e:                       # nói ra, đừng giả vờ khoẻ
            out["status"] = "degraded"
            out["error"] = str(e)[:200]
    return JSONResponse(out, status_code=200 if out["status"] == "ok" else 503)


# ── thị trường ──────────────────────────────────────────────────────────────

@app.get("/markets")
def markets():
    if IS_MOCK:
        return ok({"rows": mock.markets()})
    rows = []
    for slug in queries.market_list():
        meta = queries.market_meta(slug)
        if meta:
            rows.append({**meta, "core": queries.market_core(slug, meta["n_buildings"])})
    return ok({"rows": rows})


@app.get("/markets/{slug}")
def market_detail(slug: str):
    if IS_MOCK:
        d = mock.market_detail(slug)
        if not d:
            raise HTTPException(404, f"không có thị trường '{slug}'")
        return ok(d)
    meta = queries.market_meta(slug)
    if not meta:
        raise HTTPException(404, f"không có thị trường '{slug}'")
    n = meta["n_buildings"]
    return ok({"meta": meta, "core": queries.market_core(slug, n),
               "coverage": queries.market_coverage(slug, n),
               "forms": queries.forms(slug)})


@app.get("/markets/{slug}/buildings")
def market_buildings(
    slug: str,
    q: str | None = Query(None, max_length=120, description="tìm trong tên, dự án, địa bàn, CĐT"),
    form: str | None = Query(None, max_length=64),
    sort: str = Query("full"),
    limit: int = Query(50, ge=1, le=config.MAX_LIMIT),
    offset: int = Query(0, ge=0),
):
    fn = mock.buildings if IS_MOCK else queries.buildings
    return ok(fn(slug, q, form, sort, limit, offset))


@app.get("/markets/{slug}/metrics")
def market_metrics(slug: str, form: str | None = Query(None, max_length=64)):
    fn = mock.metrics if IS_MOCK else queries.metrics
    return ok({"rows": fn(slug, form)})


@app.get("/buildings/{code}")
def building(code: str):
    fn = mock.building if IS_MOCK else queries.building
    b = fn(code)
    if not b:
        raise HTTPException(404, f"không có toà '{code}'")
    return ok(b)


# ── Việt Nam ────────────────────────────────────────────────────────────────

@app.get("/vn/projects")
def vn_projects(
    province: str | None = Query(
        None, max_length=64,
        description="slug ASCII, ví dụ `ha-noi`, `ho-chi-minh`, `da-nang` — "
                    "lấy từ /vn/provinces. Vẫn nhận nhãn có dấu để không phá client cũ."),
    category: str | None = Query(
        None, max_length=64,
        description="slug ASCII, ví dụ `chung-cu`, `khu-do-thi` — lấy từ /vn/categories."),
    q: str | None = Query(None, max_length=120),
    sort: str = Query("full"),
    limit: int = Query(50, ge=1, le=config.MAX_LIMIT),
    offset: int = Query(0, ge=0),
):
    pi = mock.provinces_index() if IS_MOCK else vn.provinces_index()
    ci = mock.categories_index() if IS_MOCK else vn.categories_index()
    check_slug("province", province, pi)
    check_slug("category", category, ci)
    fn = mock.vn_projects if IS_MOCK else vn.projects
    return ok(fn(province, category, q, sort, limit, offset))


@app.get("/vn/projects/{eid}")
def vn_project(eid: str):
    fn = mock.vn_project if IS_MOCK else vn.project
    p = fn(eid)
    if not p:
        raise HTTPException(404, f"không có dự án '{eid}'")
    return ok(p)


@app.get("/vn/provinces")
def vn_provinces():
    """Thống kê từng tỉnh. Trường `slug` là giá trị dùng cho `?province=`."""
    fn = mock.vn_provinces if IS_MOCK else vn.provinces
    return ok({"rows": fn()})


@app.get("/vn/categories")
def vn_categories():
    """Loại dự án, kèm slug để dùng làm `?category=`."""
    if IS_MOCK:
        return ok({"rows": mock.vn_categories()})
    return ok({"rows": vn.categories()})


@app.get("/vn/tiers")
def vn_tiers():
    fn = mock.vn_tiers if IS_MOCK else vn.tiers
    return ok({"rows": fn()})


# ── tổng quan & tài liệu ────────────────────────────────────────────────────

@app.get("/overview")
def overview():
    if IS_MOCK:
        return ok(mock.overview())
    raise HTTPException(501, "chế độ real chưa nối trang tổng quan")


@app.get("/docs/search")
def docs_search(
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(20, ge=1, le=config.MAX_LIMIT),
    offset: int = Query(0, ge=0),
):
    if IS_MOCK:
        return ok(mock.docs_search(q, limit, offset))
    raise HTTPException(501, "chế độ real chưa nối tìm kiếm tài liệu")
