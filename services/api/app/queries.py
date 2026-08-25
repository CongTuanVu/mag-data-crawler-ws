"""Truy vấn thật trên parquet. Không cache, không nạp trước, trừ hai chỗ ghi rõ.

Quy tắc: mọi giá trị do người dùng gửi lên đều BIND bằng `?`, không nội suy vào
chuỗi SQL. Chỗ duy nhất ghép chuỗi là tên cột/hướng sắp xếp, và chúng được lấy từ
danh sách trắng cố định ở dưới.
"""
from __future__ import annotations

import csv
import glob
import json
import os
from functools import lru_cache

from . import config, db
from .corpus_gate import (AMEN_OK, CORE6, CORE_COND, COV_FIELDS, COV_MIN,
                          STRICT_SQL, nz)

LOOSE = lambda: f"read_parquet('{config.corpus('corpus_loose')}')"

BLD_COLS = [
    "building_name", "project_name", "admin", "address", "developer",
    "n_floors", "n_units_building", "area_m2", "area_kind", "site_area_m2",
    "price", "price_unit", "price_kind", "price_basis", "year_completed",
    "mix", "mix_kind", "building_form", "style", "handover", "amenities",
    "lat", "lon", "n_buildings", "building_code", "sources", "market",
]

# danh sách trắng cho ORDER BY — người dùng chỉ gửi được khoá, không gửi SQL
SORTS = {
    "units": "coalesce(b.n_units_building, 0) desc",
    "floors": "coalesce(b.n_floors, 0) desc",
    "year": "coalesce(b.year_completed, 0) desc",
    "area": "coalesce(b.area_m2, 0) desc",
    "price": "coalesce(b.price, 0) desc",
    "name": "b.building_name asc",
    "full": "_core desc, _strict desc, coalesce(b.n_units_building,0) desc",
}

METRICS = [
    ("floors", "Số tầng", "tầng", "n_floors", "n_floors"),
    ("units", "Số căn mỗi toà", "căn", "n_units_building", "n_units_building"),
    ("area", "Diện tích căn", "m²", "area_m2", "area_m2"),
    ("price", "Giá", "", "price", "price"),
    ("site", "Diện tích lô", "m²", "site_area_m2", "site_area_m2"),
    ("dens", "Mật độ căn", "căn/ha",
     "n_units_building/(site_area_m2/10000)", "site_area_m2"),
    ("year", "Năm hoàn thành", "", "year_completed", "year_completed"),
]


def codes_of(slug: str) -> list[str]:
    return config.MARKET_GROUPS.get(slug, [slug])


def _in_clause(codes: list[str]) -> tuple[str, list]:
    return "market in (" + ",".join("?" * len(codes)) + ")", list(codes)


# ── thị trường ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def market_list() -> list[str]:
    """Danh sách mã thị trường đã gộp nhóm. Cache: nó chỉ đổi khi parquet đổi,
    mà parquet đổi thì service được khởi động lại."""
    rows = db.q(f"select market, count(*) n from {LOOSE()} group by 1 order by n desc")
    merged = {c: g for g, cs in config.MARKET_GROUPS.items() for c in cs}
    out: list[str] = []
    for r in rows:
        k = merged.get(r["market"], r["market"])
        if k not in out:
            out.append(k)
    return out


def market_meta(slug: str) -> dict | None:
    codes = codes_of(slug)
    w, p = _in_clause(codes)
    n = db.scalar(f"select count(*) from {LOOSE()} where {w}", p)
    if not n:
        return None
    auth = db.q(f"select distinct id_authority from {LOOSE()} "
                f"where {w} and id_authority is not null", p)
    return {
        "market": slug,
        "label": config.MARKET_VI.get(slug, slug),
        "n_buildings": n,
        "id_kind": db.scalar(f"select any_value(id_kind) from {LOOSE()} where {w}", p),
        "id_authority": ", ".join(sorted(r["id_authority"] for r in auth)),
        "price_unit": db.scalar(
            f"select any_value(price_unit) from {LOOSE()} where {w} "
            f"and price_unit is not null", p),
        "price_basis": db.q(
            f"select price_basis as code, count(*) n from {LOOSE()} where {w} "
            f"and price_basis is not null group by 1 order by n desc", p),
    }


def market_core(slug: str, total: int) -> dict:
    codes = codes_of(slug)
    w, p = _in_clause(codes)
    fields = []
    for f, label in CORE6:
        n = db.scalar(f"select count(*) from {LOOSE()} where {w} and {CORE_COND[f]}", p)
        fields.append({"field": f, "label": label, "pct": round(100.0 * n / total, 1)})
    st = db.scalar(f"select count(*) from {LOOSE()} where {w} and {STRICT_SQL}", p)
    reg = db.scalar(f"select count(*) from {LOOSE()} where {w} "
                    f"and id_kind = 'official_registry'", p)
    return {
        "fields": fields, "n_pass": st, "pct": round(100.0 * st / total, 1),
        "registry_pct": round(100.0 * reg / total, 1),
        "n_have": sum(1 for x in fields if x["pct"] >= COV_MIN),
    }


def market_coverage(slug: str, total: int) -> list[dict]:
    w, p = _in_clause(codes_of(slug))
    out = []
    for f, label in COV_FIELDS:
        n = db.scalar(f"select count({f}) from {LOOSE()} where {w}", p)
        out.append({"field": f, "label": label, "pct": round(100.0 * n / total, 1)})
    return out


def buildings(slug: str, q: str | None, form: str | None, sort: str,
              limit: int, offset: int) -> dict:
    """Duyệt THẬT trên toàn kho, có phân trang — không phải mẫu dựng sẵn."""
    w, params = _in_clause(codes_of(slug))
    where = [w]
    if q:
        where.append("(b.building_name ilike ? or b.project_name ilike ? "
                     "or b.admin ilike ? or b.developer ilike ?)")
        params += [f"%{q}%"] * 4
    if form:
        where.append("(b.building_form = ? or b.style = ?)")
        params += [form, form]
    cond = " and ".join(where)

    core6 = " + ".join(
        f"case when {CORE_COND[f].replace(f, 'b.' + f, 1)} then 1 else 0 end"
        for f, _ in CORE6)
    order = SORTS.get(sort, SORTS["full"])
    cols = ", ".join("b." + c for c in BLD_COLS)

    total = db.scalar(f"select count(*) from {LOOSE()} b where {cond}", params)
    rows = db.q(
        f"""select {cols}, ({core6}) as _core,
               case when {STRICT_SQL.replace('mix', 'b.mix')} then 1 else 0 end as _strict
            from {LOOSE()} b where {cond}
            order by {order} limit ? offset ?""",
        params + [limit, offset])
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


def building(code: str) -> dict | None:
    return db.one(
        f"select * from {LOOSE()} where building_code = ? limit 1", [code])


def metrics(slug: str, form: str | None) -> list[dict]:
    """Phân bố tính LẠI theo bộ lọc đang chọn — bản tĩnh không làm được việc này."""
    w, base = _in_clause(codes_of(slug))
    extra, ep = "", []
    if form:
        extra, ep = " and (building_form = ? or style = ?)", [form, form]
    cov = {c["field"]: c["pct"] for c in market_coverage(
        slug, db.scalar(f"select count(*) from {LOOSE()} where {w}", base))}

    out = []
    for key, label, unit, expr, field in METRICS:
        if cov.get(field, 0) < COV_MIN:
            continue
        cond = f"{field} is not null"
        if key == "dens":
            cond += " and site_area_m2 > 0 and n_units_building > 0"
        p = base + ep
        sub = (f"(select {expr} x from {LOOSE()} where {w}{extra} and {cond})")
        agg = db.one(
            f"select count(*) n, quantile_cont(x,0.25) p25, median(x) med, "
            f"quantile_cont(x,0.75) p75 from {sub}", p)
        if not agg or not agg["n"] or agg["p25"] is None:
            continue
        cuts = sorted({round(db.scalar(
            f"select quantile_cont(x,{c}) from {sub}", p), 2)
            for c in (0.10, 0.30, 0.50, 0.70, 0.90)
            if db.scalar(f"select quantile_cont(x,{c}) from {sub}", p) is not None})
        bins = []
        for i in range(len(cuts) + 1):
            a = cuts[i - 1] if i else None
            b = cuts[i] if i < len(cuts) else None
            if a is None:
                c2 = f"{expr} < {b}"
            elif b is None:
                c2 = f"{expr} >= {a}"
            else:
                c2 = f"{expr} >= {a} and {expr} < {b}"
            bins.append({"lo": a, "hi": b, "n": db.scalar(
                f"select count(*) from {LOOSE()} where {w}{extra} and {cond} and {c2}", p)})
        out.append({"key": key, "label": label, "unit": unit, "n": agg["n"],
                    "p25": agg["p25"], "med": agg["med"], "p75": agg["p75"],
                    "bins": bins})
        if len(out) >= 6:
            break
    return out


def forms(slug: str) -> list[dict]:
    w, p = _in_clause(codes_of(slug))
    col = "building_form" if db.scalar(
        f"select count(building_form) from {LOOSE()} where {w}", p) else "style"
    return db.q(f"select {col} as code, count(*) n from {LOOSE()} where {w} "
                f"and {col} is not null group by 1 order by n desc", p)
