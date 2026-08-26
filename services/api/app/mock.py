"""Dữ liệu GIẢ, chỉ để dựng và kiểm hạ tầng.

Mọi thứ ở đây sinh từ một hạt cố định nên gọi bao nhiêu lần cũng ra cùng kết quả —
cần thế thì frontend mới so sánh được giữa hai lần chạy.

Hình dạng phản hồi khớp CHÍNH XÁC với bản thật ở `queries.py` / `vn.py`, còn nội
dung thì bịa. Độ lớn các con số lấy theo kho thật (618.421 toà, 7.765 dự án…) để
frontend gặp đúng cấp phân trang, nhưng TÊN và từng dòng đều là hàng mẫu.

Mọi phản hồi mang cờ `mock: true` và header `X-Data-Mode: mock`. Đừng gỡ chúng
cho tới khi `DATA_MODE=real`, nếu không ảnh chụp màn hình sẽ bị đọc nhầm là số thật.
"""
from __future__ import annotations

import hashlib

from .slugs import index as slug_index
from .slugs import resolve as slug_resolve

MARKETS = [
    ("vn", "Việt Nam", 7765, None), ("korea", "Hàn Quốc", 125373, 6),
    ("france", "Pháp", 18051, 6), ("singapore", "Singapore", 13357, 6),
    ("hongkong", "Hong Kong", 703, 6), ("uruguay", "Uruguay", 9108, 5),
    ("latvia", "Latvia", 21341, 5), ("denmark", "Đan Mạch", 28370, 5),
    ("usa", "Hoa Kỳ", 69725, 4), ("switzerland", "Thuỵ Sĩ", 152546, 4),
    ("malaysia", "Malaysia", 2327, 3), ("taiwan", "Đài Loan", 56845, 3),
    ("estonia", "Estonia", 26096, 3), ("poland", "Ba Lan", 2846, 3),
    ("georgia", "Gruzia", 2044, 3), ("netherlands", "Hà Lan", 82394, 2),
    ("kazakhstan", "Kazakhstan", 1507, 2), ("moldova", "Moldova", 266, 2),
    ("azerbaijan", "Azerbaijan", 254, 2), ("russia", "Nga", 4601, 1),
    ("uk", "Anh", 667, 1), ("japan", "Nhật Bản", 163, 2),
]
BY_SLUG = {m[0]: m for m in MARKETS}

FORMS = ["chung_cu_cao_tang", "chung_cu_trung_tang", "chung_cu_thap_tang", "biet_thu"]
PROVINCES = ["Hồ Chí Minh", "Hà Nội", "Đà Nẵng", "Đồng Nai", "Tây Ninh", "Bắc Ninh",
             "Hải Phòng", "Quảng Ninh", "Lâm Đồng", "Khánh Hòa"]
CATEGORIES = ["Chung cư", "Khu đô thị", "Nhà phố / biệt thự", "Khu công nghiệp", "Nghỉ dưỡng"]


def _r(*parts) -> float:
    """0..1 xác định, thay cho random() — cùng đầu vào luôn cho cùng số."""
    h = hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=8)
    return int.from_bytes(h.digest(), "big") / 2 ** 64


def _pick(seq, *parts):
    return seq[int(_r(*parts) * len(seq)) % len(seq)]


def markets() -> list[dict]:
    out = []
    for slug, label, n, core in MARKETS:
        m = {"market": slug, "label": label, "n_buildings": n,
             "kind": "vn" if slug == "vn" else None}
        if core is not None:
            strict = int(n * _r(slug, "strict") * 0.6)
            m["core"] = {"n_have": core, "n_pass": strict,
                         "pct": round(100.0 * strict / n, 1)}
        out.append(m)
    return out


def market_detail(slug: str) -> dict | None:
    if slug not in BY_SLUG:
        return None
    _, label, n, core = BY_SLUG[slug]
    from .corpus_gate import CORE6, COV_FIELDS
    strict = int(n * _r(slug, "strict") * 0.6)
    return {
        "meta": {"market": slug, "label": label, "n_buildings": n,
                 "id_kind": "official_registry", "id_authority": "cơ quan mẫu",
                 "price_unit": "USD",
                 "price_basis": [{"code": "measured", "n": int(n * 0.7)},
                                 {"code": "derived", "n": int(n * 0.3)}]},
        "core": {"fields": [{"field": f, "label": lb,
                             "pct": round(30 + 70 * _r(slug, f), 1)}
                            for f, lb in CORE6],
                 "n_pass": strict, "pct": round(100.0 * strict / n, 1),
                 "registry_pct": round(100 * _r(slug, "reg"), 1),
                 "n_have": core or 0},
        "coverage": [{"field": f, "label": lb, "pct": round(100 * _r(slug, "cov", f), 1)}
                     for f, lb in COV_FIELDS],
        "forms": [{"code": f, "n": int(n * _r(slug, f) / 4) + 1} for f in FORMS],
    }


def buildings(slug: str, q, form, sort, limit, offset) -> dict:
    total = BY_SLUG[slug][2] if slug in BY_SLUG else 0
    if q:
        total = max(1, int(total * 0.02))
    if form:
        total = max(1, int(total * 0.25))
    rows = []
    for i in range(offset, min(offset + limit, total)):
        s = (slug, i)
        rows.append({
            "building_code": f"{slug}-{i:06d}",
            "building_name": f"Toà mẫu {i + 1:04d}",
            "project_name": f"Dự án mẫu {i // 7 + 1:03d}",
            "admin": f"Quận mẫu {i % 12 + 1}",
            "address": f"{i % 200 + 1} Đường mẫu",
            "developer": f"CĐT mẫu {i % 40 + 1}",
            "n_floors": 3 + int(_r(*s, "fl") * 45),
            "n_units_building": 20 + int(_r(*s, "un") * 800),
            "area_m2": round(35 + _r(*s, "ar") * 90, 1),
            "site_area_m2": round(800 + _r(*s, "si") * 20000),
            "price": round(1000 + _r(*s, "px") * 9000),
            "price_unit": "USD", "price_kind": "asking_primary",
            "year_completed": 1985 + int(_r(*s, "yr") * 40),
            "building_form": _pick(FORMS, *s, "fm"),
            "mix_kind": "br_counts",
            "mix": '{"1": %d, "2": %d, "3": %d}' % (
                int(_r(*s, "m1") * 40), int(_r(*s, "m2") * 60), int(_r(*s, "m3") * 30)),
            "amenities": '["be_boi", "phong_gym", "san_choi_tre_em"]',
            "_core": 1 + int(_r(*s, "co") * 6),
            "_strict": 1 if _r(*s, "st") > 0.6 else 0,
        })
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


def building(code: str) -> dict | None:
    try:
        slug, i = code.rsplit("-", 1)
        i = int(i)
    except ValueError:
        return None
    if slug not in BY_SLUG:
        return None
    r = buildings(slug, None, None, "full", 1, i)["rows"]
    if not r:
        return None
    b = r[0]
    b["sources"] = '{"_default": "https://vi.dụ/nguon-mau"}'
    b["handover"] = "co_ban"
    b["style"] = "hien_dai"
    return b


def metrics(slug: str, form) -> list[dict]:
    spec = [("floors", "Số tầng", "tầng", 3, 50), ("units", "Số căn mỗi toà", "căn", 20, 820),
            ("area", "Diện tích căn", "m²", 35, 125), ("price", "Giá", "", 1000, 10000),
            ("site", "Diện tích lô", "m²", 800, 20800), ("dens", "Mật độ căn", "căn/ha", 20, 900)]
    n = BY_SLUG.get(slug, (None, None, 1000, None))[2]
    out = []
    for key, label, unit, lo, hi in spec:
        p25 = lo + (hi - lo) * 0.25
        med = lo + (hi - lo) * (0.42 + 0.16 * _r(slug, key, form or ""))
        p75 = lo + (hi - lo) * 0.75
        cuts = [lo + (hi - lo) * c for c in (0.10, 0.30, 0.50, 0.70, 0.90)]
        bins = []
        for i in range(len(cuts) + 1):
            a = cuts[i - 1] if i else None
            b = cuts[i] if i < len(cuts) else None
            bins.append({"lo": a and round(a, 1), "hi": b and round(b, 1),
                         "n": int(n * (0.10 + 0.06 * _r(slug, key, i)))})
        out.append({"key": key, "label": label, "unit": unit, "n": int(n * 0.8),
                    "p25": round(p25, 1), "med": round(med, 1), "p75": round(p75, 1),
                    "bins": bins})
    return out


PROV_SLUGS = slug_index(PROVINCES)
CAT_SLUGS = slug_index(CATEGORIES)


def provinces_index() -> dict[str, str]:
    return PROV_SLUGS


def categories_index() -> dict[str, str]:
    return CAT_SLUGS


def vn_projects(province, category, q, sort, limit, offset) -> dict:
    province = slug_resolve(PROV_SLUGS, province)
    category = slug_resolve(CAT_SLUGS, category)
    total = 7765
    if province:
        total = 200 + int(_r(province) * 1800)
    if category:
        total = int(total * 0.3)
    if q:
        total = max(1, int(total * 0.03))
    rows = []
    for i in range(offset, min(offset + limit, total)):
        s = ("vn", i)
        rows.append({
            "entity_id": f"mock-{i:08d}",
            "name": f"Dự án mẫu {i + 1:04d}",
            "province": province or _pick(PROVINCES, *s, "pv"),
            "district": f"Quận mẫu {i % 15 + 1}",
            "ward": f"Phường mẫu {i % 40 + 1}",
            "project_category": category or _pick(CATEGORIES, *s, "ct"),
            "developer": f"CĐT mẫu {i % 60 + 1}",
            "n_units": 50 + int(_r(*s, "un") * 1500),
            "n_floors": 3 + int(_r(*s, "fl") * 35),
            "n_buildings": 1 + int(_r(*s, "nb") * 12),
            "site_area_m2": round(2000 + _r(*s, "si") * 90000),
            "site_coverage_pct": round(25 + _r(*s, "cv") * 40, 1),
            "price_per_m2_vnd": round((22 + _r(*s, "px") * 60) * 1e6),
            "lat": round(8.5 + _r(*s, "la") * 14.8, 5),
            "lon": round(102.2 + _r(*s, "lo") * 7.1, 5),
            "n_listings": int(_r(*s, "nl") * 400),
            "_core": 1 + int(_r(*s, "co") * 8),
        })
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


def vn_project(eid: str) -> dict | None:
    if not eid.startswith("mock-"):
        return None
    i = int(eid.split("-")[1])
    p = vn_projects(None, None, None, "full", 1, i)["rows"]
    if not p:
        return None
    d = p[0]
    d["amenities"] = '[{"amenity": "Bể bơi", "scope": "noi_khu"}, {"amenity": "Phòng gym", "scope": "noi_khu"}]'
    d["unit_types"] = '[{"n_bedrooms": 1, "n_units": 40}, {"n_bedrooms": 2, "n_units": 120}]'
    d["buildings"] = [{"building_name": f"Toà {chr(65 + k)}", "n_floors": 20 + k,
                       "n_units": 180 + k * 20} for k in range(int(_r(eid, "nb") * 4) + 1)]
    d["listings"] = vn_listings(eid)
    return d


def vn_listings(eid: str, per: int = 5) -> dict:
    n = 5 + int(_r(eid, "nl") * 300)
    return {
        "n": n, "n_px": int(n * 0.4), "n_ar": n,
        "px25": 44e6, "px50": 56e6, "px75": 71e6,
        "ar25": 52.0, "ar50": 68.0, "ar75": 88.0, "pr50": 3.6e9,
        "br": [{"k": 1, "n": int(n * .2)}, {"k": 2, "n": int(n * .5)},
               {"k": 3, "n": int(n * .3)}],
        "deal": [{"k": "ban", "n": int(n * .85)}, {"k": "cho_thue", "n": int(n * .15)}],
        "rows": [{"title": f"Tin rao mẫu {k + 1} — căn {2 + k % 2} phòng ngủ",
                  "deal_type": "ban", "area_m2": 55.0 + k * 7,
                  "n_bedrooms": 2 + k % 2, "price_vnd": (3.2 + k * .4) * 1e9,
                  "unit_price_vnd_m2": (52 + k) * 1e6, "floor": 8 + k}
                 for k in range(per)],
    }


def vn_provinces() -> list[dict]:
    out = []
    inv = {v: k for k, v in PROV_SLUGS.items()}
    for i, pv in enumerate(PROVINCES):
        n = 100 + int(_r(pv) * 1900)
        out.append({"slug": inv[pv], "province": pv, "n": n, "n_geo": int(n * 0.99),
                    "med_units": 200 + int(_r(pv, "u") * 400),
                    "med_floors": 8 + round(_r(pv, "f") * 20, 1),
                    "med_cover": round(30 + _r(pv, "c") * 25, 1),
                    "med_site_ha": round(1 + _r(pv, "s") * 20, 2),
                    "med_dens": 40 + int(_r(pv, "d") * 400),
                    "med_px": round(12 + _r(pv, "p") * 40, 1),
                    "n_list": int(_r(pv, "l") * 20000)})
    out.sort(key=lambda x: -x["n"])
    return out


def vn_categories() -> list[dict]:
    inv = {v: k for k, v in CAT_SLUGS.items()}
    return [{"slug": inv[c], "label": c, "n": 400 + int(_r(c) * 2500)}
            for c in CATEGORIES]


def vn_tiers() -> list[dict]:
    return [{"label": "Dự án", "n": 7765},
            {"label": "Toà nhà", "n": 3849, "up": 1276, "parent": "dự án"},
            {"label": "Căn hộ", "n": 10510, "up": 10510, "parent": "toà"},
            {"label": "Tin rao", "n": 168946, "up": 67341, "parent": "dự án"}]


def overview() -> dict:
    return {
        "corpus": {"loose": 618421, "strict": 157384, "strict_pct": 25.4,
                   "n_markets": 20,
                   "tables": [{"name": "dim_project", "n": 657208, "cols": 37},
                              {"name": "fact_building", "n": 618421, "cols": 31},
                              {"name": "corpus_loose", "n": 618421, "cols": 67},
                              {"name": "corpus_strict", "n": 157384, "cols": 67}],
                   "vn_tables": [{"name": "vn_project", "n": 7765, "cols": 65},
                                 {"name": "vn_building", "n": 3849, "cols": 46},
                                 {"name": "vn_unit", "n": 10510, "cols": 23},
                                 {"name": "vn_listing", "n": 168946, "cols": 39}]},
        "vn_tiers": vn_tiers(), "vn_provinces": len(PROVINCES),
        "japan": {"n_buildings": 163, "n_projects": 141, "n_rows": 5980},
        "docs": {"label": "Bộ dữ liệu tài liệu", "n_docs": 22559, "mb": 837.5,
                 "n_domains": 1198, "n_langs": 161, "n_jobs": 72, "with_url": 22521},
    }


def docs_search(q: str, limit: int, offset: int) -> dict:
    total = 40 + int(_r(q) * 400)
    rows = [{
        "doc_id": 1000 + i,
        "title": f"Tài liệu mẫu {i + 1} về {q}",
        "domain": _pick(["batdongsan.com.vn", "cafef.vn", "vi.wikipedia.org",
                         "vietnamnet.vn", "scmp.com"], q, i),
        "lang": _pick(["vi", "en", "zh", "ja", "ko"], q, i, "l"),
        "url": f"https://vi.du/tai-lieu-mau-{i + 1}",
        "root": "mag" if i % 3 else "kđt",
        "snippet": f"…đoạn văn mẫu có chứa [{q}] để kiểm giao diện…",
    } for i in range(offset, min(offset + limit, total))]
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}
