#!/usr/bin/env python3
"""Nhật Bản — nguồn duy nhất không nằm trong corpus parquet.

Gọi từ build_market.py; không chạy độc lập.

163 toà dựng tay trong `output_csv/*.csv`, mỗi toà một file ở dạng long: cột
`bang` tách bản ghi thành B1 (toà) · B2 (loại căn) · B5 (hạng mục bàn giao) ·
B6 (tiện ích) · B7 (giá). Đây chính là tập benchmark mà repo này sinh ra,
chưa được merge vào corpus, nên phải dựng riêng rồi ép về ĐÚNG hình dạng mà
`build_market()` trả về — trang không cần biết nó đến từ đâu.

Ba chỗ nguồn này khác hẳn corpus, đã đo chứ không đoán:

  · Giá là **JPY mỗi CĂN** (166/166 dòng B7 đều `price_unit=per_unit`), không
    phải mỗi m² như các thị trường khác. Không quy đổi sang m² vì diện tích chỉ
    phủ 17% — quy đổi sẽ bịa mẫu số cho 83% còn lại.
  · Không có `handover` dạng MỨC (thô / cơ bản / full). Thay vào đó là 2.788
    hạng mục bàn giao rời, chi tiết hơn nhưng khác hình. Suy ngược ra mức là
    đoán, nên để trống trường `handover` và đưa hạng mục vào `handover_items`.
  · Không có toạ độ, không có phong cách kiến trúc: 0/163 cả hai.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re

JP_COUNTRY = {"japan", "nhật bản", "nhat ban"}

# nhóm tiện ích của nguồn Nhật → mã nhóm mà trang đang dùng
AMEN_GROUP = {
    "do_xe": "do_xe", "dich_vu_le_tan": "dich_vu", "the_thao_gym": "the_thao",
    "cong_dong_su_kien": "cong_dong", "suc_khoe_spa": "suc_khoe",
    "tre_em": "tre_em", "thu_cung": "thu_cung", "canh_quan_vuon": "canh_quan",
    "khong_gian_lam_viec": "lam_viec", "ha_tang": "ha_tang",
}
HAND_GROUP = {
    "an_ninh_pccc": "An ninh & PCCC", "thiet_bi_dien": "Thiết bị điện",
    "bep": "Bếp", "thiet_bi_ve_sinh": "Thiết bị vệ sinh",
    "dieu_hoa_thong_gio": "Điều hoà & thông gió", "san": "Sàn",
    "cua": "Cửa", "tuong_tran": "Tường & trần", "khac": "Khác",
}

COV_FIELDS = [("n_floors", "số tầng"), ("n_units_building", "số căn"),
              ("area_m2", "diện tích căn"), ("price", "giá"),
              ("site_area_m2", "diện tích lô"), ("lat", "toạ độ"),
              ("mix", "cơ cấu căn"), ("year_completed", "năm hoàn thành"),
              ("amenities", "tiện ích"), ("building_form", "loại hình"),
              ("style", "phong cách")]

CORE6 = [("mix", "cơ cấu căn"), ("area_m2", "diện tích căn"), ("price", "giá"),
         ("amenities", "tiện ích"), ("style", "phong cách"), ("handover", "bàn giao")]

REQUIRED = [("floors", "Số tầng", "tầng", "n_floors"),
            ("units", "Số căn mỗi toà", "căn", "n_units_building"),
            ("area", "Diện tích căn", "m²", "area_m2"),
            ("price", "Giá", "", "price")]
FILLERS = [("site", "Diện tích lô", "m²", "site_area_m2"),
           ("dens", "Mật độ căn", "căn/ha", None),
           ("year", "Năm hoàn thành", "", "year_completed"),
           ("amen", "Số tiện ích", "mục", "amenities")]
N_METRICS, COV_MIN = 6, 50.0


def _f(s):
    """chuỗi CSV → số, hoặc None. Nguồn có cả '8324.91' lẫn '1,234' lẫn rỗng."""
    s = (s or "").strip().replace(",", "")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v


def _s(x):
    x = (x or "").strip()
    return x or None


def _q(xs, p):
    """phân vị nội suy tuyến tính, cùng nghĩa với quantile_cont của DuckDB"""
    if not xs:
        return None
    v = sorted(xs)
    if len(v) == 1:
        return v[0]
    i = p * (len(v) - 1)
    lo = int(i)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def _read(csv_dir):
    """gom mọi file về một dict building_id → {b1, b2[], b5[], b6[], b7[]}"""
    out = {}
    for path in sorted(glob.glob(os.path.join(csv_dir, "*.csv"))):
        if os.path.basename(path).startswith("_"):
            continue
        try:
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig", newline="")))
        except Exception:
            continue
        head = next((r for r in rows if r.get("bang") == "B1"), None)
        if not head:
            continue
        if (head.get("country") or "").strip().lower() not in JP_COUNTRY:
            continue
        bid = head.get("building_id") or os.path.basename(path)[:-4]
        d = out.setdefault(bid, {"b1": head, "b2": [], "b5": [], "b6": [], "b7": []})
        for r in rows:
            k = {"B2": "b2", "B5": "b5", "B6": "b6", "B7": "b7"}.get(r.get("bang"))
            if k:
                d[k].append(r)
    return out


def _building(bid, d):
    h = d["b1"]

    # ── diện tích căn: bình quân các loại căn có số đo, không phải min/max khai ở B1
    areas = [_f(r.get("area_net_m2")) for r in d["b2"]]
    areas = [a for a in areas if a]
    area = round(sum(areas) / len(areas), 1) if areas else None

    # ── cơ cấu: đếm LOẠI CĂN theo số phòng ngủ (nguồn không khai số căn mỗi loại)
    br = {}
    for r in d["b2"]:
        b = _f(r.get("bedrooms"))
        if b is None or b > 10:            # có 2 dòng ghi nhầm 'bedrooms=60'
            continue
        br[str(int(b))] = br.get(str(int(b)), 0) + 1
    mix = json.dumps(br, ensure_ascii=False) if br else None

    # ── giá: trung vị của các quan sát, quy về một con số JPY mỗi căn
    px = []
    for r in d["b7"]:
        lo, hi, av = _f(r.get("price_min")), _f(r.get("price_max")), _f(r.get("price_avg"))
        v = av if av else ((lo + hi) / 2 if lo and hi else (lo or hi))
        if v:
            px.append(v)
    price = round(_q(px, 0.5)) if px else None

    amen, aseen = [], set()
    for r in d["b6"]:
        nm = _s(r.get("amenity_name")) or _s(r.get("record_label"))
        if nm and nm not in aseen:
            aseen.add(nm)
            amen.append(nm)
    # `item_spec` KHÔNG phải spec của hạng mục — đã kiểm: nó là blob thô của trang
    # nguồn, lặp nguyên văn cho mọi hạng mục cùng file. Bỏ, chỉ giữ tên + nhóm.
    seen, hand = set(), []
    for r in d["b5"]:
        nm, ct = _s(r.get("record_label")) or _s(r.get("item_name")), _s(r.get("item_category"))
        if nm and (nm, ct) not in seen:
            seen.add((nm, ct))
            hand.append({"n": nm, "c": ct})

    src = {}
    for i, u in enumerate((h.get("source_urls") or "").split(";")):
        u = u.strip()
        if u.startswith("http"):
            src["_default" if not src else f"nguồn {i + 1}"] = u

    return {
        "building_name": _s(h.get("building_name_local")) or _s(h.get("building_name")),
        "name_latin": _s(h.get("building_name")),
        "project_name": _s(h.get("project_name")),
        "admin": _s(h.get("district")) or _s(h.get("city")),
        "address": _s(h.get("address")),
        "developer": _s(h.get("developer")),
        "n_floors": _f(h.get("num_floors_above")),
        "n_units_building": _f(h.get("num_units_total")),
        "area_m2": area,
        "area_kind": "thong_thuy" if area else None,
        "site_area_m2": _f(h.get("land_area_m2")),
        "price": price,
        "price_unit": "JPY/can" if price else None,
        "price_kind": "asking_primary" if price else None,
        "price_basis": "listing" if price else None,
        "year_completed": _f(h.get("year_handover")),
        "mix": mix,
        "mix_kind": "br_type_counts" if mix else None,
        "building_form": _s(h.get("building_type")),
        "style": None,
        "handover": None,
        "handover_items": hand or None,
        "amenities": json.dumps(amen, ensure_ascii=False) if amen else None,
        "lat": None, "lon": None,
        "n_buildings": _f(h.get("num_towers")),
        "building_code": bid,
        "sources": json.dumps(src, ensure_ascii=False) if src else None,
        "gfa_m2": _f(h.get("gfa_m2")),
        "n_basements": _f(h.get("num_basements")),
        "status": _s(h.get("status")),
        "architect": _s(h.get("architect_firm")),
        "signature": _s(h.get("signature_features")),
        "confidence": _s(h.get("confidence")),
    }


def _val(b, key):
    if key == "dens":
        s, u = b.get("site_area_m2"), b.get("n_units_building")
        return u / (s / 10000) if s and u and s > 0 else None
    if key == "amen":
        a = b.get("amenities")
        return len(json.loads(a)) if a else None
    return b.get({"floors": "n_floors", "units": "n_units_building",
                  "area": "area_m2", "price": "price",
                  "site": "site_area_m2", "year": "year_completed"}[key])


def _metric(blds, key, label, unit, edge, num):
    xs = [v for v in (_val(b, key) for b in blds) if v is not None]
    if not xs:
        return None
    p25, med, p75 = _q(xs, 0.25), _q(xs, 0.5), _q(xs, 0.75)
    cuts = sorted({round(_q(xs, q), 2) for q in (0.10, 0.30, 0.50, 0.70, 0.90)})
    bins = []
    for i in range(len(cuts) + 1):
        a = cuts[i - 1] if i else None
        b = cuts[i] if i < len(cuts) else None
        if a is None:
            lab, hit = f"< {edge(b)}", [x for x in xs if x < b]
        elif b is None:
            lab, hit = f"≥ {edge(a)}", [x for x in xs if x >= a]
        else:
            lab, hit = f"{edge(a)}–{edge(b)}", [x for x in xs if a <= x < b]
        bins.append({"label": lab, "lo": num(a), "hi": num(b), "n": len(hit)})
    return {"key": key, "label": label, "unit": unit, "n": len(xs),
            "p25": num(p25), "med": num(med), "p75": num(p75), "bins": bins}


def build_jp(csv_dir, edge, num):
    data = _read(csv_dir)
    if not data:
        return None, {}
    blds = [_building(bid, d) for bid, d in sorted(data.items())]
    tot = len(blds)

    have = lambda f: sum(1 for b in blds if b.get(f) not in (None, "", "[]", "{}"))
    cov = [{"field": f, "label": lb, "pct": round(100.0 * have(f) / tot, 1)}
           for f, lb in COV_FIELDS]
    pct = {c["field"]: c["pct"] for c in cov}

    metrics = []
    for key, lb, u, base in REQUIRED:
        if pct.get(base, 0) >= COV_MIN:
            mt = _metric(blds, key, lb, u, edge, num)
            if mt:
                metrics.append(mt)
    for key, lb, u, base in FILLERS:
        if len(metrics) >= N_METRICS:
            break
        if base is None or pct.get(base, 0) >= COV_MIN:
            mt = _metric(blds, key, lb, u, edge, num)
            if mt:
                metrics.append(mt)
    metrics = metrics[:N_METRICS]
    mkeys = {m["key"] for m in metrics}

    # Trường `handover` để trống có chủ đích (nguồn không khai MỨC). Nhưng chiều
    # "bàn giao" thì nguồn CÓ, dưới dạng hạng mục, cho 99,4% toà — chấm nó 0% là
    # nói sai về dữ liệu. Chấm theo bằng chứng, và ghi rõ đang chấm cái gì.
    n_hand = sum(1 for b in blds if b.get("handover_items"))
    core = []
    for f, lb in CORE6:
        c = {"field": f, "label": lb, "pct": pct.get(f, round(100.0 * have(f) / tot, 1))}
        if f == "handover":
            c["pct"] = round(100.0 * n_hand / tot, 1)
            c["shape"] = "hạng mục, không phải mức"
        core.append(c)

    # điểm xếp hạng trong danh sách bên trái — cùng ngữ nghĩa với thị trường corpus
    for b in blds:
        b["_core"] = sum(1 for f, _ in CORE6 if b.get(f) not in (None, "", "[]", "{}"))
        b["_strict"] = 0
        b["_met"] = sum(1 for k in mkeys if _val(b, k) is not None)
        b["_full"] = sum(1 for f in ("n_floors", "n_units_building", "area_m2", "price",
                                     "year_completed", "developer", "amenities",
                                     "building_form", "style", "handover", "mix",
                                     "lat", "site_area_m2")
                         if b.get(f) not in (None, "", "[]", "{}"))
        for k, v in list(b.items()):
            if isinstance(v, float) and v == int(v):
                b[k] = int(v)

    n_px = sum(1 for b in blds if b.get("price") is not None)
    # tổng số DÒNG của nguồn Nhật, để trang tổng quan cộng cùng đơn vị với các kho khác
    n_rows = tot + sum(len(d["b2"]) + len(d["b5"]) + len(d["b6"]) + len(d["b7"])
                       for d in data.values())
    meta = {"market": "japan", "label": "Nhật Bản", "n_buildings": tot,
            "n_projects": len({b["project_name"] for b in blds if b.get("project_name")}),
            "id_authority": "MAG WS1 — hồ sơ dựng tay", "id_kind": "curated_case",
            "price_unit": "JPY/can", "tables": "output_csv/*.csv — B1·B2·B5·B6·B7",
            "n_rows": n_rows,
            "price_basis": [{"code": "listing", "n": n_px}] if n_px else []}

    out = {"meta": meta, "coverage": cov, "metrics": metrics,
           "core": {"fields": core, "n_pass": 0, "pct": 0.0, "registry_pct": 0.0,
                    "n_have": sum(1 for x in core if x["pct"] >= COV_MIN),
                    "note": ("Ô <b>bàn giao</b> chấm theo <b>hạng mục</b> nguồn khai "
                             f"({n_hand}/{tot} toà), không phải theo MỨC thô/cơ bản/full — "
                             "nguồn không khai mức, và suy ngược ra là đoán. "
                             "Tập này <b>chưa được merge vào corpus</b> — nó là hồ sơ dựng tay của "
                             "chính repo mag-data-crawler-ws, chưa đi qua đường ống sinh "
                             "<span class=\"mono\">corpus_loose</span>. Cổng strict vì thế chưa "
                             "chấm nó, chứ không phải nó trượt.")},
           "defaults": {}, "buildings": [{k: v for k, v in b.items() if v is not None}
                                         for b in blds]}

    # nhãn của nguồn này không nằm trong dim_enum — nạp thẳng vào bảng nhãn chung
    E = {"amenities": {}, "price_unit": {"JPY/can": {"vi": "JPY mỗi căn"}},
         "id_kind": {"curated_case": {"vi": "Hồ sơ dựng tay"}},
         "building_form": {"chung_cu": {"vi": "Chung cư"}},
         "price_kind": {"asking_primary": {"vi": "Giá chào bán sơ cấp"}},
         "mix_kind": {"br_type_counts": {
             "vi": "Theo số phòng ngủ — đếm LOẠI căn",
             "def": ("Nguồn khai từng loại căn nhưng không khai mỗi loại có bao nhiêu căn, "
                     "nên con số ở đây là SỐ LOẠI căn, không phải số căn.")}}}
    for d in data.values():
        for r in d["b6"]:
            nm = _s(r.get("amenity_name")) or _s(r.get("record_label"))
            if nm and nm not in E["amenities"]:
                E["amenities"][nm] = {
                    "vi": nm, "local": _s(r.get("amenity_name_local")),
                    "group": AMEN_GROUP.get(_s(r.get("amenity_category")), "khac")}
    return out, E
