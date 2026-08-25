"""Việt Nam — bốn cấp, có toạ độ nên khác hẳn các thị trường khác."""
from __future__ import annotations

import html
from functools import lru_cache

from . import config, db
from .slugs import index as slug_index
from .slugs import resolve as slug_resolve

P = lambda n: f"read_parquet('{config.corpus(n)}')"

PROJ_COLS = [
    "entity_id", "name", "province", "district", "ward", "address", "developer",
    "project_category", "project_type", "legal", "progress",
    "site_area_m2", "site_coverage_pct", "site_coverage_computed_pct", "far",
    "footprint_area_m2", "gross_floor_area_m2", "n_floors", "n_units",
    "n_buildings", "n_basements", "n_elevators_min", "n_elevators_max",
    "units_per_floor_min", "units_per_floor_max", "population",
    "price_per_m2_vnd", "price_kind", "mix_kind", "lat", "lon",
    "n_sources", "n_conflicts", "start_raw", "finish_raw",
    "amenities", "unit_types", "land_use", "products", "sources",
]

SORTS = {
    "units": "coalesce(n_units,0) desc", "floors": "coalesce(n_floors,0) desc",
    "site": "coalesce(site_area_m2,0) desc", "name": "name asc",
    "full": "_core desc, coalesce(n_units,0) desc",
}


def _clean(v):
    """Tên trong nguồn còn lẫn thực thể HTML (`T&amp;T`) — gỡ về chữ thật."""
    return html.unescape(v) if isinstance(v, str) else v


def _rows(sql, params=()):
    return [{k: _clean(v) for k, v in r.items()} for r in db.q(sql, params)]


@lru_cache(maxsize=1)
def provinces_index() -> dict[str, str]:
    """slug → tên tỉnh thật. Dựng từ chính dữ liệu, xếp theo số dự án giảm dần
    nên tỉnh lớn giữ slug trần khi có đụng độ."""
    rows = db.q(f"select province, count(*) n from {P('vn_project')} "
                f"where province is not null group by 1 order by n desc")
    return slug_index([_clean(r["province"]) for r in rows])


@lru_cache(maxsize=1)
def categories_index() -> dict[str, str]:
    rows = db.q(f"select project_category k, count(*) n from {P('vn_project')} "
                f"where project_category is not null group by 1 order by n desc")
    return slug_index([_clean(r["k"]) for r in rows])


def projects(province: str | None, category: str | None, q: str | None,
             sort: str, limit: int, offset: int) -> dict:
    province = slug_resolve(provinces_index(), province)
    category = slug_resolve(categories_index(), category)
    where, params = ["1=1"], []
    if province:
        where.append("province = ?"); params.append(province)
    if category:
        where.append("project_category = ?"); params.append(category)
    if q:
        where.append("(name ilike ? or address ilike ? or developer ilike ? "
                     "or district ilike ? or ward ilike ?)")
        params += [f"%{q}%"] * 5
    cond = " and ".join(where)
    core = ["site_area_m2", "n_units", "n_floors", "site_coverage_pct",
            "n_buildings", "amenities", "unit_types", "price_per_m2_vnd"]
    score = " + ".join(f"case when {c} is null then 0 else 1 end" for c in core)
    cols = ", ".join(PROJ_COLS)
    total = db.scalar(f"select count(*) from {P('vn_project')} where {cond}", params)
    rows = _rows(
        f"select {cols}, ({score}) as _core, "
        f"(select count(*) from {P('vn_listing')} l where l.entity_id = p.entity_id) as n_listings "
        f"from {P('vn_project')} p where {cond} "
        f"order by {SORTS.get(sort, SORTS['full'])} limit ? offset ?",
        params + [limit, offset])
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


def project(eid: str) -> dict | None:
    cols = ", ".join(PROJ_COLS)
    r = _rows(f"select {cols} from {P('vn_project')} where entity_id = ? limit 1", [eid])
    if not r:
        return None
    out = r[0]
    out["buildings"] = _rows(
        f"""select building_name, name_display, n_floors, n_units, n_basements,
               site_area_m2, gross_floor_area_m2, floor_efficiency, notice_no, source
            from {P('vn_building')} where entity_id = ?
            order by coalesce(n_units,0) desc""", [eid])
    out["listings"] = listings_of(eid)
    return out


def listings_of(eid: str, per: int = 5) -> dict:
    """Thống kê trước, rồi vài tin lấy ngẫu nhiên NHƯNG lặp lại được:
    xếp theo `hash(source_key)` chứ không `random()`."""
    st = db.one(
        f"""select count(*) n, count(unit_price_vnd_m2) n_px,
               quantile_cont(unit_price_vnd_m2,0.25) px25,
               median(unit_price_vnd_m2) px50,
               quantile_cont(unit_price_vnd_m2,0.75) px75,
               count(area_m2) n_ar, quantile_cont(area_m2,0.25) ar25,
               median(area_m2) ar50, quantile_cont(area_m2,0.75) ar75,
               median(price_vnd) pr50
            from {P('vn_listing')} where entity_id = ?""", [eid])
    if not st or not st["n"]:
        return {"n": 0, "rows": []}
    st["br"] = db.q(f"select n_bedrooms k, count(*) n from {P('vn_listing')} "
                    f"where entity_id = ? and n_bedrooms is not null "
                    f"group by 1 order by 1", [eid])
    st["deal"] = db.q(f"select deal_type k, count(*) n from {P('vn_listing')} "
                      f"where entity_id = ? and deal_type is not null "
                      f"group by 1 order by n desc", [eid])
    st["rows"] = _rows(
        f"""select title, listing_kind, deal_type, area_m2, n_bedrooms,
               price_vnd, unit_price_vnd_m2, floor
            from (select *, row_number() over (
                    order by hash(coalesce(source_key, href, title))) rn
                  from {P('vn_listing')} where entity_id = ?)
            where rn <= ?""", [eid, per])
    return st


@lru_cache(maxsize=1)
def provinces() -> list[dict]:
    """Thống kê từng tỉnh — KHÔNG chọn dự án đại diện. Chọn một dự án làm đại diện
    tỉnh là suy đoán; bảng này chỉ báo cáo phân bố thật."""
    rows = _rows(f"""
        with p as (
          select province, lat, n_units, n_floors, site_coverage_pct, site_area_m2,
                 price_per_m2_vnd, entity_id,
                 case when site_area_m2 > 0 and n_units > 0
                      then n_units / (site_area_m2/10000) end as dens
          from {P('vn_project')} where province is not null
        ), lst as (
          select p2.province, count(*) n from {P('vn_listing')} l
          join (select entity_id, province from {P('vn_project')}
                where province is not null) p2 using (entity_id) group by 1
        )
        select p.province, count(*) n, count(p.lat) n_geo,
               round(median(p.n_units)) med_units,
               round(median(p.n_floors),1) med_floors,
               round(median(p.site_coverage_pct),1) med_cover,
               round(median(p.site_area_m2)/10000,2) med_site_ha,
               round(median(p.dens)) med_dens,
               round(median(p.price_per_m2_vnd)/1e6,1) med_px,
               coalesce(any_value(lst.n),0) n_list
        from p left join lst on lst.province = p.province
        group by 1 order by n desc""")
    inv = {v: k for k, v in provinces_index().items()}
    for r in rows:
        r["slug"] = inv.get(r["province"], "")
    return rows


@lru_cache(maxsize=1)
def categories() -> list[dict]:
    inv = {v: k for k, v in categories_index().items()}
    rows = db.q(f"select project_category k, count(*) n from {P('vn_project')} "
                f"where project_category is not null group by 1 order by n desc")
    return [{"slug": inv.get(_clean(r["k"]), ""), "label": _clean(r["k"]), "n": r["n"]}
            for r in rows]


@lru_cache(maxsize=1)
def tiers() -> list[dict]:
    """Thang bốn cấp đo theo CHA TRỰC TIẾP, không phải 'về dự án' cho tất cả:
    căn nối lên TOÀ (building_key), toà và tin rao nối lên DỰ ÁN (entity_id)."""
    n_proj = db.scalar(f"select count(*) from {P('vn_project')}")
    n_bld = db.scalar(f"select count(*) from {P('vn_building')}")
    n_unit = db.scalar(f"select count(*) from {P('vn_unit')}")
    n_list = db.scalar(f"select count(*) from {P('vn_listing')}")
    bld_up = db.scalar(f"select count(*) from {P('vn_building')} b "
                       f"join {P('vn_project')} p using (entity_id)")
    unit_up = db.scalar(f"select count(*) from {P('vn_unit')} u join "
                        f"(select distinct building_key from {P('vn_building')}) b "
                        f"using (building_key)")
    list_up = db.scalar(f"select count(*) from {P('vn_listing')} l "
                        f"join {P('vn_project')} p using (entity_id)")
    return [
        {"label": "Dự án", "n": n_proj},
        {"label": "Toà nhà", "n": n_bld, "up": bld_up, "parent": "dự án"},
        {"label": "Căn hộ", "n": n_unit, "up": unit_up, "parent": "toà"},
        {"label": "Tin rao", "n": n_list, "up": list_up, "parent": "dự án"},
    ]
