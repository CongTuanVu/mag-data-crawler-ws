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
    from . import docs as docsdb
    from . import jp, queries, vn

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
    rows = queries.markets()
    if jp.available():
        m = jp.meta()
        rows.append({**m["meta"], "core": m["core"]})
    t = vn.tiers()
    rows.insert(0, {"market": "vn", "label": "Việt Nam", "kind": "vn",
                    "n_projects": t[0]["n"], "n_buildings": t[1]["n"],
                    "n_units": t[2]["n"], "n_listings": t[3]["n"]})
    return ok({"rows": rows})


@app.get("/markets/{slug}")
def market_detail(slug: str):
    if IS_MOCK:
        d = mock.market_detail(slug)
        if not d:
            raise HTTPException(404, f"không có thị trường '{slug}'")
        return ok(d)
    if slug == "japan":
        if not jp.available():
            raise HTTPException(404, "không đọc được nguồn Nhật")
        return ok(jp.meta())
    d = queries.market_detail(slug)
    if not d:
        raise HTTPException(404, f"không có thị trường '{slug}'")
    return ok(d)


@app.get("/markets/{slug}/buildings")
def market_buildings(
    slug: str,
    q: str | None = Query(None, max_length=120, description="tìm trong tên, dự án, địa bàn, CĐT"),
    form: str | None = Query(None, max_length=64),
    sort: str = Query("full"),
    limit: int = Query(50, ge=1, le=config.MAX_LIMIT),
    offset: int = Query(0, ge=0),
):
    if IS_MOCK:
        return ok(mock.buildings(slug, q, form, sort, limit, offset))
    if slug == "japan":
        return ok(jp.buildings(q, form, sort, limit, offset))
    return ok(queries.buildings(slug, q, form, sort, limit, offset))


@app.get("/markets/{slug}/metrics")
def market_metrics(slug: str, form: str | None = Query(None, max_length=64)):
    if IS_MOCK:
        return ok({"rows": mock.metrics(slug, form)})
    if slug == "japan":
        return ok({"rows": []})          # Nhật chưa dựng phân bố
    return ok({"rows": queries.metrics(slug, form)})


@app.get("/buildings/{code}")
def building(code: str):
    b = mock.building(code) if IS_MOCK else (
        queries.building(code) or (jp.building(code) if jp.available() else None))
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
    try:
        return ok(fn(province, category, q, sort, limit, offset))
    except ValueError as e:                    # tầng truy vấn từ chối bộ lọc lạ
        raise HTTPException(422, str(e))


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


@app.get("/vn/metrics")
def vn_metrics(category: str | None = Query(None, max_length=64)):
    """Phân bố tính RIÊNG theo loại dự án — trộn mọi loại thì mật độ căn trung vị
    lệch hơn 5 lần."""
    if IS_MOCK:
        return ok({"rows": mock.metrics("vn", category)})
    try:
        return ok({"rows": vn.metrics(category)})
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/vn/coverage")
def vn_coverage():
    if IS_MOCK:
        return ok({"rows": []})
    return ok({"rows": vn.coverage()})


@app.get("/vn/map")
def vn_map():
    """Chấm toạ độ THẬT của mọi dự án, gom theo tỉnh. ~110 KB, đổi rất hiếm —
    client nên nhớ lại thay vì gọi mỗi lần vẽ."""
    if IS_MOCK:
        return ok({"w": 430, "h": 560, "n": 0, "total": 0, "prov": {}, "rest": ""})
    return ok(vn.map_points())


@app.get("/enums")
def enums():
    """Nhãn hiển thị: mã → cụm từ tiếng Việt. Không hardcode ở client."""
    if IS_MOCK:
        return ok({"enums": {}})
    from . import config as C
    rows = db_enum()
    if jp.available():
        rows.setdefault("amenities", {}).update(jp.enums())
    return ok({"enums": rows})


def db_enum() -> dict:
    from . import db
    import json as _json
    out: dict = {}
    try:
        rows = db.q(f"select field, code, label_vi, label_en, definition_vi, "
                    f"sort_order, attrs from read_parquet('{config.corpus('dim_enum')}') "
                    f"where status = 'active' order by field, sort_order")
    except Exception:
        return out
    for r in rows:
        g = None
        if r["attrs"]:
            try:
                g = _json.loads(r["attrs"]).get("group")
            except Exception:
                pass
        out.setdefault(r["field"], {})[r["code"]] = {
            "vi": r["label_vi"], "en": r["label_en"],
            "def": r["definition_vi"], "order": r["sort_order"], "group": g}
    return out


# ── tổng quan & tài liệu ────────────────────────────────────────────────────

@app.get("/overview")
def overview():
    if IS_MOCK:
        return ok(mock.overview())
    mk = queries.markets()
    t = vn.tiers()
    from . import db as _db

    def _tbl(name, gran):
        """Số dòng và số cột đọc thẳng từ parquet — trang tổng quan cộng chúng
        lại thành tổng bản ghi, nên không được đoán."""
        try:
            path = config.corpus(name)
            n = _db.scalar(f"select count(*) from read_parquet('{path}')")
            cols = len(_db.q(f"describe select * from read_parquet('{path}') limit 0"))
            return {"name": name, "n": n, "cols": cols, "gran": gran}
        except Exception:
            return None

    tables = [x for x in (
        _tbl("dim_project", "1 dòng = 1 dự án / khu"),
        _tbl("fact_building", "1 dòng = 1 toà"),
        _tbl("corpus_loose", "1 dòng = 1 toà, đã nối sẵn"),
        _tbl("corpus_strict", "1 dòng = 1 toà ĐẠT CHUẨN")) if x]
    vn_tables = [x for x in (
        _tbl("vn_project", "1 dòng = 1 dự án"), _tbl("vn_building", "1 dòng = 1 toà"),
        _tbl("vn_unit", "1 dòng = 1 căn"), _tbl("vn_listing", "1 dòng = 1 tin rao")) if x]

    out = {
        "corpus": {
            "tables": tables, "vn_tables": vn_tables,
            "loose": sum(m["n_buildings"] for m in mk),
            "strict": sum(m["core"]["n_pass"] for m in mk),
            "n_markets": len(mk),
            "by_market": [{"k": m["label"], "n": m["n_buildings"],
                           "core": m["core"]["n_have"],
                           "strict": m["core"]["n_pass"]} for m in mk],
        },
        "vn_tiers": t, "vn_provinces": len(vn.provinces()),
    }
    out["corpus"]["strict_pct"] = round(
        100.0 * out["corpus"]["strict"] / max(out["corpus"]["loose"], 1), 1)
    if jp.available():
        j = jp.meta()["meta"]
        out["japan"] = {"n_buildings": j["n_buildings"],
                        "n_projects": j["n_projects"], "n_rows": j["n_rows"]}
    d = docsdb.stats()
    if d:
        out["docs"] = {"label": "Bộ dữ liệu tài liệu", **d}
    return ok(out)


@app.get("/docs/search")
def docs_search(
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(20, ge=1, le=config.MAX_LIMIT),
    offset: int = Query(0, ge=0),
):
    if IS_MOCK:
        return ok(mock.docs_search(q, limit, offset))
    return ok(docsdb.search(q, limit, offset))
