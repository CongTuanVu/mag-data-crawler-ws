#!/usr/bin/env python3
"""Dữ liệu trang Việt Nam — bốn cấp và có toạ độ, nên khác hẳn các thị trường khác.

Gọi từ build_market.py; không chạy độc lập.

Ba khác biệt đã đo, quyết định thiết kế:
  · toạ độ 99,9% ở cấp dự án → vẽ được bản đồ điểm thật
  · phân theo `project_category` đổi kết quả 5 lần: căn/ha của riêng chung cư là
    316·568·840, trộn tất cả loại chỉ còn 108,6 → mọi phân bố tính THEO LOẠI
  · thang bốn cấp KHÔNG phải một chuỗi liền. Mỗi cấp chỉ nối được lên cấp CHA
    của nó, và chỗ đứt nằm ở giữa:
        toà  → dự án : 1.276/3.849 dòng  (179 dự án có toà)
        căn  → toà   : 10.510/10.510 dòng (67 toà) — nối đủ 100%
        tin  → dự án : 67.341/168.946 dòng (2.997 dự án có tin)
    Toàn bộ 10.510 căn đến từ nguồn Sở Xây dựng, mà nhánh `sxd:` của bảng toà
    có `entity_id` rỗng (870/875 dòng) → chuỗi căn→toà→dự án dừng ở cấp toà.
    Đo trực tiếp: chỉ 2/7.765 dự án chạm được tới cấp căn. Không phải lỗi join
    — nối theo tên cũng chỉ khớp 3/37 tên dự án mà nguồn căn có, và phần còn
    lại đa nghĩa (4 dự án tên "Hưng Phát"), nên KHÔNG suy đoán.
"""
from __future__ import annotations

import html
import json
import math


def _clean(s):
    """Tên trong nguồn còn lẫn thực thể HTML (`T&amp;T`) — gỡ về chữ thật."""
    return html.unescape(s) if isinstance(s, str) else s

# trường thiết kế của Việt Nam — KHÔNG phải sáu trường lõi của corpus_strict
# (Việt Nam không có trong corpus_strict; xem CATALOG: "Không có Việt Nam")
VN_FIELDS = [
    ("site_area_m2", "diện tích lô"), ("n_units", "số căn"), ("n_floors", "số tầng"),
    ("site_coverage_pct", "mật độ xây dựng"), ("n_buildings", "số toà"),
    ("amenities", "tiện ích"), ("unit_types", "cơ cấu căn"),
    ("price_per_m2_vnd", "giá"), ("far", "hệ số sử dụng đất"),
    ("n_elevators_min", "thang máy"), ("lat", "toạ độ"),
    ("province", "tỉnh / thành"), ("district", "quận / huyện"), ("ward", "phường / xã"),
]

VN_METRICS = [
    ("dens", "Mật độ căn", "căn/ha", "n_units/(site_area_m2/10000)",
     "site_area_m2 > 0 and n_units > 0"),
    ("cover", "Mật độ xây dựng", "%", "site_coverage_pct", "site_coverage_pct is not null"),
    ("floors", "Số tầng", "tầng", "n_floors", "n_floors is not null"),
    ("units", "Số căn", "căn", "n_units", "n_units is not null"),
    ("site", "Diện tích lô", "m²", "site_area_m2", "site_area_m2 is not null"),
    ("land", "Đất mỗi căn", "m²", "site_area_m2/n_units", "site_area_m2 > 0 and n_units > 0"),
]

PROJ_FIELDS = [
    "entity_id", "name", "province", "district", "ward", "address", "developer",
    "project_category", "project_type", "legal", "progress",
    "site_area_m2", "site_coverage_pct", "site_coverage_computed_pct", "far",
    "footprint_area_m2", "gross_floor_area_m2", "n_floors", "n_units", "n_buildings",
    "n_basements", "n_elevators_min", "n_elevators_max",
    "units_per_floor_min", "units_per_floor_max", "population",
    "price_per_m2_vnd", "price_kind", "price_raw", "mix_kind",
    "lat", "lon", "n_sources", "n_conflicts", "start_raw", "finish_raw",
    "amenities", "unit_types", "land_use", "products", "sources",
]

# khung chiếu bản đồ, khớp với dải đất liền Việt Nam
LON0, LON1, LAT0, LAT1 = 102.0, 110.0, 8.0, 23.6
MAP_H = 560.0
MAP_W = MAP_H * ((LON1 - LON0) * math.cos(math.radians(16))) / (LAT1 - LAT0)

# Đất liền Việt Nam hẹp và dài (276 × 560) nên ô bản đồ thành một dải mỏng.
# Nới KHUNG NGANG chứ không phóng to hình: tỷ lệ chiếu giữ nguyên, chấm giữ
# nguyên cỡ, chỉ thêm lề trống hai bên. Chiều cao vẫn là thứ định tỷ lệ vẽ.
MAP_BOX_W = 430.0
MAP_DX = (MAP_BOX_W - MAP_W) / 2


def _xy(lon, lat):
    x = MAP_DX + (lon - LON0) / (LON1 - LON0) * MAP_W
    y = (LAT1 - lat) / (LAT1 - LAT0) * MAP_H
    return round(x, 1), round(y, 1)


def _edge(v, fmt_edge):
    return fmt_edge(v)


def build_vn(con, corpus, sample, fmt_edge, num_fn):
    P = corpus.rstrip("/")
    S = f"'{P}/vn_project.parquet'"
    B = f"'{P}/vn_building.parquet'"
    U = f"'{P}/vn_unit.parquet'"
    L = f"'{P}/vn_listing.parquet'"
    one = lambda q: con.sql(q).fetchone()

    n_proj = one(f"select count(*) from {S}")[0]
    n_bld = one(f"select count(*) from {B}")[0]
    n_unit = one(f"select count(*) from {U}")[0]
    n_list = one(f"select count(*) from {L}")[0]

    # ── thang bốn cấp ────────────────────────────────────────────────────────
    # Mỗi cấp đo theo CHA TRỰC TIẾP của nó, không phải "về dự án" cho tất cả:
    # căn nối lên TOÀ (bằng building_key), toà và tin rao nối lên DỰ ÁN
    # (bằng entity_id). Đo nhầm cha là lý do trước đây căn hiện 2/7.765.
    bld_up = one(f"select count(*) from {B} b join {S} p using (entity_id)")[0]
    bld_par = one(f"""select count(distinct p.entity_id) from {S} p where exists
        (select 1 from {B} b where b.entity_id = p.entity_id)""")[0]
    unit_up = one(f"""select count(*) from {U} u
        join (select distinct building_key from {B}) b using (building_key)""")[0]
    unit_par = one(f"""select count(distinct u.building_key) from {U} u
        join (select distinct building_key from {B}) b using (building_key)""")[0]
    # căn đi tiếp lên dự án được bao nhiêu — con số đứt gãy, phải nói ra
    unit_to_proj = one(f"""select count(distinct p.entity_id) from {S} p where exists
        (select 1 from {B} b join {U} u on u.building_key = b.building_key
         where b.entity_id = p.entity_id)""")[0]
    unit_provs = one(f"select count(distinct province) from {U} where province is not null")[0]
    list_up = one(f"select count(*) from {L} l join {S} p using (entity_id)")[0]
    list_par = one(f"""select count(distinct p.entity_id) from {S} p where exists
        (select 1 from {L} l where l.entity_id = p.entity_id)""")[0]

    tiers = [
        {"label": "Dự án", "tbl": "vn_project", "n": n_proj,
         "note": "gốc — mọi cấp khác quy về đây"},
        {"label": "Toà nhà", "tbl": "vn_building", "n": n_bld,
         "up": bld_up, "parent": "dự án", "par_n": bld_par, "par_tot": n_proj,
         "par_unit": "dự án"},
        {"label": "Căn hộ", "tbl": "vn_unit", "n": n_unit,
         "up": unit_up, "parent": "toà", "par_n": unit_par, "par_tot": n_bld,
         "par_unit": "toà",
         "stop": ("Chuỗi dừng ở đây: nhánh Sở Xây dựng của bảng toà không mang mã dự án, "
                  f"nên chỉ {unit_to_proj}/{format(n_proj, ',d').replace(',', '.')} dự án "
                  f"chạm tới cấp căn. Toàn bộ căn nằm trong {unit_provs} tỉnh.")},
        {"label": "Tin rao", "tbl": "vn_listing", "n": n_list,
         "up": list_up, "parent": "dự án", "par_n": list_par, "par_tot": n_proj,
         "par_unit": "dự án"},
    ]

    cats = [r[0] for r in con.sql(
        f"select coalesce(project_category, '(chưa rõ)') k, count(*) n from {S} "
        f"group by 1 order by n desc").fetchall()]

    def metrics_for(where):
        out = []
        for key, label, unit, expr, cond in VN_METRICS:
            sub = f"(select {expr} x from {S} where {cond} and {where})"
            n, p25, med, p75 = one(
                f"select count(*), quantile_cont(x,0.25), median(x), quantile_cont(x,0.75) from {sub}")
            if not n or n < 40 or p25 is None:
                continue
            cuts = sorted({round(one(f"select quantile_cont(x,{q}) from {sub}")[0], 2)
                           for q in (0.10, 0.30, 0.50, 0.70, 0.90)
                           if one(f"select quantile_cont(x,{q}) from {sub}")[0] is not None})
            bins = []
            for i in range(len(cuts) + 1):
                a = cuts[i - 1] if i else None
                b = cuts[i] if i < len(cuts) else None
                if a is None:
                    c2, lab = f"{expr} < {b}", f"< {_edge(b, fmt_edge)}"
                elif b is None:
                    c2, lab = f"{expr} >= {a}", f"≥ {_edge(a, fmt_edge)}"
                else:
                    c2, lab = f"{expr} >= {a} and {expr} < {b}", f"{_edge(a, fmt_edge)}–{_edge(b, fmt_edge)}"
                bins.append({"label": lab, "lo": a, "hi": b,
                             "n": one(f"select count(*) from {S} where {cond} and {where} and {c2}")[0]})
            out.append({"key": key, "label": label, "unit": unit, "n": n,
                        "p25": round(p25, 2), "med": round(med, 2), "p75": round(p75, 2),
                        "bins": bins})
        return out

    by_cat = {"": metrics_for("1=1")}
    for k in cats:
        w = ("project_category is null" if k == "(chưa rõ)"
             else "project_category = '%s'" % k.replace("'", "''"))
        m = metrics_for(w)
        if m:
            by_cat[k] = m

    coverage = []
    for f, label in VN_FIELDS:
        n = one(f"select count({f}) from {S}")[0]
        coverage.append({"field": f, "label": label, "pct": round(100.0 * n / n_proj, 1)})

    return {
        "meta": {"market": "vn", "label": "Việt Nam", "kind": "vn",
                 "n_projects": n_proj, "n_buildings": n_bld,
                 "n_units": n_unit, "n_listings": n_list,
                 "tiers": tiers,
                 "link_buildings": bld_par, "link_listings": list_par,
                 "map": {"w": round(MAP_BOX_W, 1), "h": MAP_H,
                         "land_w": round(MAP_W, 1), "dx": round(MAP_DX, 1)}},
        "coverage": coverage,
        "categories": cats,
        "metrics_by_cat": by_cat,
    }


def vn_projects(con, corpus, sample):
    """Mẫu dự án, trải đều theo phân vị số căn, ưu tiên đủ trường thiết kế."""
    P = corpus.rstrip("/")
    S = f"'{P}/vn_project.parquet'"
    core = ["site_area_m2", "n_units", "n_floors", "site_coverage_pct",
            "n_buildings", "amenities", "unit_types", "price_per_m2_vnd"]
    score = " + ".join(f"case when p.{f} is null then 0 else 1 end" for f in core)
    cols = ", ".join("p." + f for f in PROJ_FIELDS)
    per = max(1, sample // 20)
    q = f"""
    with base as (
      select {cols}, ({score}) as _core,
        (select count(*) from '{P}/vn_building.parquet' b where b.entity_id = p.entity_id) as _n_bld,
        (select count(*) from '{P}/vn_listing.parquet' l where l.entity_id = p.entity_id) as _n_list,
        ntile(20) over (order by coalesce(p.n_units, 0)) as _t
      from {S} p
    ), ranked as (
      select *, row_number() over (partition by _t
        order by _core desc, _n_list desc, coalesce(n_units,0) desc) as _r
      from base
    )
    select * exclude (_t, _r) from ranked where _r <= {per}
    """
    r = con.sql(q)
    names = list(r.columns)
    out = []
    for row in r.fetchall():
        d = dict(zip(names, row))
        if d.get("lat") is not None and d.get("lon") is not None:
            d["_x"], d["_y"] = _xy(d["lon"], d["lat"])
        out.append({k: _clean(v) for k, v in d.items() if v is not None})
    return out


def vn_map_path(con, corpus):
    """Nền bản đồ = chấm của MỌI dự án có toạ độ. Không có file bản đồ nào:
    hình đất nước là do chính mật độ dự án tạo ra.

    Điểm được GOM THEO TỈNH chứ không nhân bản: trang ghép các đoạn lại thành
    nền, và tô riêng một tỉnh bằng chính đoạn của tỉnh đó."""
    P = corpus.rstrip("/")
    rows = con.sql(f"""select lon, lat, province from '{P}/vn_project.parquet'
        where lat between {LAT0} and {LAT1} and lon between {LON0} and {LON1}""").fetchall()
    seen, prov, rest, n = set(), {}, [], 0
    for lon, lat, pv in rows:
        x, y = _xy(lon, lat)
        if (x, y) in seen:
            continue
        seen.add((x, y))
        n += 1
        seg = f"M{x} {y}h.01"
        if pv:
            prov.setdefault(_clean(pv), []).append(seg)
        else:
            rest.append(seg)
    return {"prov": {k: "".join(v) for k, v in prov.items()},
            "rest": "".join(rest), "n": n,
            "total": con.sql(f"select count(*) from '{P}/vn_project.parquet' "
                             f"where lat is not null").fetchone()[0]}


def vn_buildings_of(con, corpus, entity_ids):
    """Toà thuộc các dự án trong mẫu — chỉ 179/7.765 dự án có, nên rất thưa."""
    if not entity_ids:
        return {}
    P = corpus.rstrip("/")
    inl = "', '".join(str(e).replace("'", "''") for e in entity_ids)
    rows = con.sql(f"""select entity_id, building_name, name_display, n_floors, n_units,
        n_basements, site_area_m2, gross_floor_area_m2, floor_efficiency, skips_floor_13,
        notice_no, notice_date, source
        from '{P}/vn_building.parquet' where entity_id in ('{inl}')
        order by coalesce(n_units, 0) desc""").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r[0], []).append({
            "name": r[2] or r[1], "n_floors": r[3], "n_units": r[4], "n_basements": r[5],
            "site_area_m2": r[6], "gfa": r[7], "floor_eff": r[8], "skip13": r[9],
            "notice": r[10], "notice_date": r[11], "source": r[12]})
    return out


def vn_listings_of(con, corpus, entity_ids, per=5):
    """Tin rao của mỗi dự án: THỐNG KÊ trước, rồi vài tin lấy ngẫu nhiên.

    Ngẫu nhiên nhưng lặp lại được: xếp theo `hash(source_key)` thay vì
    `random()`, nên build lại cho cùng kết quả — không phải tin to nhất hay
    mới nhất, chỉ là một lát cắt không thiên vị."""
    if not entity_ids:
        return {}
    P = corpus.rstrip("/")
    L = f"'{P}/vn_listing.parquet'"
    inl = "', '".join(str(e).replace("'", "''") for e in entity_ids)
    WH = f"entity_id in ('{inl}')"

    st = con.sql(f"""
      select entity_id, count(*) n,
             count(unit_price_vnd_m2) n_px,
             round(quantile_cont(unit_price_vnd_m2, 0.25)) px25,
             round(median(unit_price_vnd_m2)) px50,
             round(quantile_cont(unit_price_vnd_m2, 0.75)) px75,
             count(area_m2) n_ar,
             round(quantile_cont(area_m2, 0.25), 1) ar25,
             round(median(area_m2), 1) ar50,
             round(quantile_cont(area_m2, 0.75), 1) ar75,
             round(median(price_vnd)) pr50
      from {L} where {WH} group by 1""").fetchall()
    out = {}
    for r in st:
        out[r[0]] = {"n": r[1],
                     "px": [r[3], r[4], r[5]] if r[2] else None, "n_px": r[2],
                     "ar": [r[7], r[8], r[9]] if r[6] else None, "n_ar": r[6],
                     "pr": r[10], "br": [], "deal": [], "rows": []}

    for col, key in (("n_bedrooms", "br"), ("deal_type", "deal")):
        for eid, v, n in con.sql(f"""
              select entity_id, {col}, count(*) n from {L}
              where {WH} and {col} is not null group by 1,2 order by 1, n desc""").fetchall():
            if eid in out:
                out[eid][key].append([v, n])

    rows = con.sql(f"""
      select entity_id, title, listing_kind, deal_type, area_m2, n_bedrooms,
             price_vnd, unit_price_vnd_m2, floor, ward_slug
      from (select *, row_number() over (partition by entity_id
              order by hash(coalesce(source_key, href, title))) rn
            from {L} where {WH})
      where rn <= {per}""").fetchall()
    for r in rows:
        if r[0] in out:
            out[r[0]]["rows"].append({
                "title": _clean(r[1]), "kind": r[2], "deal": r[3], "area": r[4],
                "br": r[5], "price": r[6], "unit_price": r[7], "floor": r[8],
                "ward": _clean(r[9])})
    return out


def vn_by_province(con, corpus):
    """Thống kê từng tỉnh — KHÔNG chọn dự án đại diện.

    Chọn một dự án làm đại diện tỉnh là một phép suy đoán (7/75 tỉnh lệch trung
    vị quá 50% vì không đủ dự án hoàn chỉnh để chọn). Bảng này chỉ báo cáo phân
    bố thật của tỉnh: đếm, độ phủ toạ độ, và trung vị từng chỉ tiêu.
    """
    P = corpus.rstrip("/")
    S = f"'{P}/vn_project.parquet'"
    L = f"'{P}/vn_listing.parquet'"
    q = f"""
    with p as (
      select province, lat, n_units, n_floors, site_coverage_pct, site_area_m2,
             price_per_m2_vnd, entity_id,
             case when site_area_m2 > 0 and n_units > 0
                  then n_units / (site_area_m2 / 10000) end as dens
      from {S} where province is not null
    ), lst as (
      select p2.province, count(*) n from {L} l
      join (select entity_id, province from {S} where province is not null) p2
        using (entity_id) group by 1
    )
    select p.province,
           count(*) as n,
           count(p.lat) as n_geo,
           round(median(p.n_units)) as med_units,
           round(median(p.n_floors), 1) as med_floors,
           round(median(p.site_coverage_pct), 1) as med_cover,
           round(median(p.site_area_m2) / 10000, 2) as med_site_ha,
           round(median(p.dens)) as med_dens,
           round(median(p.price_per_m2_vnd) / 1e6, 1) as med_px,
           coalesce(any_value(lst.n), 0) as n_list
    from p left join lst on lst.province = p.province
    group by 1 order by n desc
    """
    r = con.sql(q)
    names = list(r.columns)
    out = []
    for row in r.fetchall():
        d = dict(zip(names, row))
        out.append({k: _clean(v) for k, v in d.items() if v is not None})
    return out
