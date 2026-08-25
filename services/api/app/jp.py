"""Nhật Bản — nguồn duy nhất KHÔNG nằm trong parquet.

163 toà dựng tay trong `output_csv/*.csv`, mỗi toà một file dạng long: cột `bang`
tách thành B1 (toà) · B2 (loại căn) · B5 (bàn giao) · B6 (tiện ích) · B7 (giá).

Đọc một lần lúc khởi động rồi giữ trong bộ nhớ: 163 toà, ~6.000 dòng, vài trăm KB.
Quét lại 163 file CSV cho mỗi request thì mới là phí.
"""
from __future__ import annotations

import csv
import glob
import html
import json
import os
from functools import lru_cache

from . import config

JP_COUNTRY = {"japan", "nhật bản", "nhat ban"}
AMEN_GROUP = {
    "do_xe": "do_xe", "dich_vu_le_tan": "dich_vu", "the_thao_gym": "the_thao",
    "cong_dong_su_kien": "cong_dong", "suc_khoe_spa": "suc_khoe",
    "tre_em": "tre_em", "thu_cung": "thu_cung", "canh_quan_vuon": "canh_quan",
    "khong_gian_lam_viec": "lam_viec", "ha_tang": "ha_tang",
}


def _f(s):
    s = (s or "").strip().replace(",", "")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _s(x):
    x = html.unescape((x or "").strip())
    return x or None


@lru_cache(maxsize=1)
def _load() -> tuple[list[dict], dict, int]:
    """(danh sách toà, bảng nhãn tiện ích, tổng số dòng nguồn)"""
    files: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(config.JP_CSV_DIR, "*.csv"))):
        if os.path.basename(path).startswith("_"):
            continue
        try:
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig", newline="")))
        except OSError:
            continue
        head = next((r for r in rows if r.get("bang") == "B1"), None)
        if not head or (head.get("country") or "").strip().lower() not in JP_COUNTRY:
            continue
        bid = head.get("building_id") or os.path.basename(path)[:-4]
        d = files.setdefault(bid, {"b1": head, "b2": [], "b5": [], "b6": [], "b7": []})
        for r in rows:
            k = {"B2": "b2", "B5": "b5", "B6": "b6", "B7": "b7"}.get(r.get("bang"))
            if k:
                d[k].append(r)

    out, enums, n_rows = [], {}, 0
    for bid, d in sorted(files.items()):
        h = d["b1"]
        n_rows += 1 + sum(len(d[k]) for k in ("b2", "b5", "b6", "b7"))

        areas = [a for a in (_f(r.get("area_net_m2")) for r in d["b2"]) if a]
        br: dict[str, int] = {}
        for r in d["b2"]:
            b = _f(r.get("bedrooms"))
            if b is not None and b <= 10:
                br[str(int(b))] = br.get(str(int(b)), 0) + 1

        px = []
        for r in d["b7"]:
            lo, hi, av = _f(r.get("price_min")), _f(r.get("price_max")), _f(r.get("price_avg"))
            v = av or ((lo + hi) / 2 if lo and hi else (lo or hi))
            if v:
                px.append(v)
        px.sort()

        amen, seen = [], set()
        for r in d["b6"]:
            nm = _s(r.get("amenity_name")) or _s(r.get("record_label"))
            if nm and nm not in seen:
                seen.add(nm)
                amen.append(nm)
                enums.setdefault(nm, {
                    "vi": nm, "local": _s(r.get("amenity_name_local")),
                    "group": AMEN_GROUP.get(_s(r.get("item_category"))
                                            or _s(r.get("amenity_category")), "khac")})
        hand, hseen = [], set()
        for r in d["b5"]:
            nm, ct = _s(r.get("record_label")) or _s(r.get("item_name")), _s(r.get("item_category"))
            if nm and (nm, ct) not in hseen:
                hseen.add((nm, ct))
                hand.append({"n": nm, "c": ct})

        out.append({
            "building_code": bid,
            "building_name": _s(h.get("building_name_local")) or _s(h.get("building_name")),
            "name_latin": _s(h.get("building_name")),
            "project_name": _s(h.get("project_name")),
            "admin": _s(h.get("district")) or _s(h.get("city")),
            "address": _s(h.get("address")), "developer": _s(h.get("developer")),
            "n_floors": _f(h.get("num_floors_above")),
            "n_units_building": _f(h.get("num_units_total")),
            "area_m2": round(sum(areas) / len(areas), 1) if areas else None,
            "area_kind": "thong_thuy" if areas else None,
            "site_area_m2": _f(h.get("land_area_m2")),
            "price": round(px[len(px) // 2]) if px else None,
            "price_unit": "JPY/can" if px else None,
            "price_kind": "asking_primary" if px else None,
            "year_completed": _f(h.get("year_handover")),
            "mix": json.dumps(br, ensure_ascii=False) if br else None,
            "mix_kind": "br_type_counts" if br else None,
            "building_form": _s(h.get("building_type")),
            "style": None, "handover": None,
            "handover_items": hand or None,
            "amenities": json.dumps(amen, ensure_ascii=False) if amen else None,
            "n_basements": _f(h.get("num_basements")),
            "architect": _s(h.get("architect_firm")),
            "market": "japan",
        })
    return out, enums, n_rows


def all_rows() -> list[dict]:
    return _load()[0]


def enums() -> dict:
    return _load()[1]


def n_rows() -> int:
    return _load()[2]


def _core(b: dict) -> int:
    """Sáu trường lõi. `handover` chấm theo HẠNG MỤC vì nguồn không khai MỨC —
    suy ngược ra mức thô/cơ bản/full là đoán."""
    got = [b.get("mix"), b.get("area_m2"), b.get("price"),
           b.get("amenities"), b.get("style"), b.get("handover_items")]
    return sum(1 for g in got if g not in (None, "", "[]", "{}"))


def available() -> bool:
    return bool(all_rows())


def meta() -> dict:
    rows = all_rows()
    n = len(rows)
    if not n:
        # Thư mục CSV chưa mount hoặc rỗng — nói ra, đừng chia cho 0 rồi 500.
        return {"meta": {"market": "japan", "label": "Nhật Bản", "n_buildings": 0,
                         "error": f"không đọc được toà nào ở {config.JP_CSV_DIR}"},
                "core": {"fields": [], "n_pass": 0, "pct": 0.0,
                         "registry_pct": 0.0, "n_have": 0}}
    fields = [("mix", "cơ cấu căn"), ("area_m2", "diện tích căn"), ("price", "giá"),
              ("amenities", "tiện ích"), ("style", "phong cách"),
              ("handover_items", "bàn giao")]
    core = []
    for f, lb in fields:
        c = sum(1 for b in rows if b.get(f) not in (None, "", "[]", "{}"))
        e = {"field": "handover" if f == "handover_items" else f,
             "label": lb, "pct": round(100.0 * c / n, 1)}
        if f == "handover_items":
            e["shape"] = "hạng mục, không phải mức"
        core.append(e)
    return {
        "meta": {"market": "japan", "label": "Nhật Bản", "n_buildings": n,
                 "n_projects": len({b["project_name"] for b in rows if b.get("project_name")}),
                 "id_kind": "curated_case", "id_authority": "MAG WS1 — hồ sơ dựng tay",
                 "price_unit": "JPY/can", "n_rows": n_rows(),
                 "tables": "output_csv/*.csv — B1·B2·B5·B6·B7"},
        "core": {"fields": core, "n_pass": 0, "pct": 0.0, "registry_pct": 0.0,
                 "n_have": sum(1 for x in core if x["pct"] >= 50.0),
                 "note": "Tập này chưa được merge vào corpus — cổng strict chưa "
                         "chấm nó, chứ không phải nó trượt."},
    }


def buildings(q, form, sort, limit, offset) -> dict:
    rows = [dict(b, _core=_core(b), _strict=0) for b in all_rows()]
    if q:
        s = q.lower()
        rows = [b for b in rows if any(
            s in str(b.get(k) or "").lower()
            for k in ("building_name", "name_latin", "project_name", "admin", "developer"))]
    if form:
        rows = [b for b in rows if b.get("building_form") == form]
    key = {"units": lambda b: -(b.get("n_units_building") or 0),
           "floors": lambda b: -(b.get("n_floors") or 0),
           "year": lambda b: -(b.get("year_completed") or 0),
           "area": lambda b: -(b.get("area_m2") or 0),
           "price": lambda b: -(b.get("price") or 0),
           "name": lambda b: (b.get("name_latin") or ""),
           }.get(sort, lambda b: (-b["_core"], -(b.get("n_units_building") or 0)))
    rows.sort(key=key)
    return {"total": len(rows), "limit": limit, "offset": offset,
            "rows": rows[offset:offset + limit]}


def building(code: str) -> dict | None:
    for b in all_rows():
        if b["building_code"] == code:
            return dict(b, _core=_core(b), _strict=0)
    return None
