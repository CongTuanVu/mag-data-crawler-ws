"""Lắp ráp record thô của LLM thành 7 bảng đúng khoá + cột derived.

Ba việc, đều deterministic (chạy lại trên cùng raw → cùng output):
  1. sinh khoá (building_id, unit_type_id, room_id, price_id…) và nối FK
  2. tính cột derived — feature_spec §13 quy tắc 5: bảng con là nguồn sự thật,
     trường tóm tắt ở B1 luôn tính lại, không nhập tay
  3. dựng bảng provenance dài (1 dòng = 1 giá trị) và bảng phẳng building_benchmark
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config, schema

Rows = List[Dict[str, Any]]


# ── tiện ích ────────────────────────────────────────────────────────────────
def _pairs_to_obj(pairs: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    if not pairs:
        return None
    return {p["key"]: p["value"] for p in pairs if p.get("key")}


def _uniq(key: str, used: set) -> str:
    if key not in used:
        used.add(key)
        return key
    i = 2
    while f"{key}_{i}" in used:
        i += 1
    used.add(f"{key}_{i}")
    return f"{key}_{i}"


def _pct(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den in (None, 0):
        return None
    return round(num / den * 100, 2)


def _file_index(manifest: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    """tên file .txt -> (url, accessed_at); ảnh mặt bằng cũng được lập chỉ mục."""
    idx: Dict[str, Tuple[str, str]] = {}
    for s in manifest.get("sources", []):
        meta = (s.get("url", ""), s.get("accessed_at", ""))
        for k in ("text_file", "raw_file", "shot_file"):
            if s.get(k):
                idx[Path(s[k]).name] = meta
                idx[s[k]] = meta
        for fp in s.get("floorplan_files") or []:
            idx[Path(fp).name] = meta
            idx[fp] = meta
    return idx


class Provenance:
    def __init__(self, file_idx: Dict[str, Tuple[str, str]]) -> None:
        self.rows: Rows = []
        self.idx = file_idx

    def add(self, table: str, key: str, record: Dict[str, Any],
            entries: Optional[List[Dict[str, Any]]], default_conf: str = "medium") -> None:
        for p in entries or []:
            fname = p.get("source_file") or ""
            url, accessed = self.idx.get(fname, self.idx.get(Path(fname).name, ("", "")))
            val = record.get(p.get("field"))
            self.rows.append({
                "table": table, "record_key": key, "field": p.get("field"),
                "value": _flat(val), "source_url": url, "source_file": fname,
                "snippet": (p.get("snippet") or "").replace("\n", " ")[:300],
                "confidence": p.get("confidence") or default_conf, "accessed_at": accessed,
            })


def _flat(v: Any) -> Any:
    if isinstance(v, list):
        return "; ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return v


# ── lắp ráp ─────────────────────────────────────────────────────────────────
def assemble(building_id: str, resolved: Dict[str, Any], text_out: Dict[str, Any],
             fp_out: Dict[str, Any], verified: Rows, manifest: Dict[str, Any],
             linked_case_id: Optional[str] = None, is_target: bool = False
             ) -> Tuple[Dict[str, Rows], Rows, List[str], Rows]:
    prov = Provenance(_file_index(manifest))
    warn: List[str] = []
    tables: Dict[str, Rows] = {name: [] for name in schema.TABLES}

    # ---- B2 unit_type ------------------------------------------------------
    used, code2id = set(), {}
    for rec in text_out["unit_type"]["records"]:
        code = (rec.get("type_code") or "").strip()
        if not code:
            warn.append("B2: bỏ 1 record thiếu type_code")
            continue
        uid = _uniq(f"{building_id}__{config.slugify(code, 'type')}", used)
        code2id[code] = uid
        row = {k: rec.get(k) for k in schema.TABLES["unit_type"].llm_names}
        row.update(unit_type_id=uid, building_id=building_id,
                   ratio_net_gross_pct=_pct(rec.get("area_net_m2"), rec.get("area_gross_m2")),
                   floorplan_file=None)
        tables["unit_type"].append(row)
        prov.add("unit_type", uid, row, rec.get("provenance"))

    def resolve_type(code: Optional[str]) -> Optional[str]:
        if not code:
            return None
        if code in code2id:
            return code2id[code]
        low = {k.lower(): v for k, v in code2id.items()}
        return low.get(code.strip().lower())

    # ---- B3 unit_room: vision + dòng người đã xác nhận ---------------------
    room_rows, used_rooms = [], set()

    def push_room(uid: str, r: Dict[str, Any], source_file: str, conf: str, src_type: str) -> None:
        code = (r.get("room_code") or config.slugify(r.get("room_type") or "room"))
        rid = _uniq(f"{uid}__{config.slugify(code, 'room')}", used_rooms)
        row = {"room_id": rid, "unit_type_id": uid, "room_code": code,
               "room_type": r.get("room_type"), "room_label_raw": r.get("room_label_raw"),
               "area_m2": r.get("area_m2"), "width_m": r.get("width_m"),
               "length_m": r.get("length_m"), "has_window": r.get("has_window"),
               "is_ensuite": r.get("is_ensuite"), "position_note": r.get("position_note"),
               "source_type": src_type}
        room_rows.append(row)
        prov.add("unit_room", rid, row, [
            {"field": f, "source_file": source_file,
             "snippet": (r.get("label_on_drawing") or r.get("room_label_raw") or "")[:300],
             "confidence": conf}
            for f in ("room_type", "area_m2") if row.get(f) is not None])

    verified_keys = {(v.get("type_code"), v.get("room_code")) for v in verified}
    for plan in fp_out.get("plans", []):
        if not plan.get("is_floorplan") or plan.get("plan_kind") != "unit":
            continue
        uid = resolve_type(plan.get("matched_type_code"))
        if not uid:
            warn.append(f"B3: {plan['source_file']} không khớp type_code nào "
                        f"(nhãn trên bản vẽ: {plan.get('unit_label_raw')}) → bỏ {len(plan['rooms'])} phòng")
            continue
        for r in plan["rooms"]:
            if (plan.get("matched_type_code"), r.get("room_code")) in verified_keys:
                continue                                   # đã có bản người xác nhận
            push_room(uid, r, plan["source_file"], "low", "floorplan_image")
    for v in verified:
        uid = resolve_type(v.get("type_code"))
        if not uid:
            warn.append(f"B3: dòng xác nhận có type_code '{v.get('type_code')}' không khớp B2")
            continue
        conv = {k: (float(v[k]) if v.get(k) not in (None, "") else None)
                for k in ("area_m2", "width_m", "length_m")}
        conv.update({k: (str(v.get(k)).lower() in {"true", "yes", "1"}
                         if v.get(k) not in (None, "") else None)
                     for k in ("has_window", "is_ensuite")})
        push_room(uid, {**v, **conv}, v.get("source_file") or "manual", "high", "manual")
    tables["unit_room"] = room_rows

    # ---- B4 floor_plate ----------------------------------------------------
    used = set()
    for rec in text_out["floor_plate"]["records"]:
        tw = rec.get("tower_code") or "main"
        fr = rec.get("floor_range") or "na"
        fid = _uniq(f"{building_id}__{config.slugify(tw, 'main')}__{config.slugify(fr, 'na')}", used)
        row = {k: rec.get(k) for k in schema.TABLES["floor_plate"].llm_names}
        row["unit_type_mix"] = _pairs_to_obj(rec.get("unit_type_mix"))
        eff = _pct(rec.get("nfa_per_floor_m2"), rec.get("gfa_per_floor_m2"))
        upe = (round(rec["units_per_floor"] / rec["num_elevators"], 2)
               if rec.get("units_per_floor") and rec.get("num_elevators") else None)
        row.update(floor_plate_id=fid, building_id=building_id,
                   efficiency_per_floor_pct=eff, units_per_elevator=upe)
        tables["floor_plate"].append(row)
        prov.add("floor_plate", fid, row, rec.get("provenance"))
    if not tables["floor_plate"]:
        for plan in fp_out.get("plans", []):
            if plan.get("plan_kind") == "floor" and plan.get("units_on_floor"):
                tw, fr = plan.get("tower_code") or "main", plan.get("floor_range") or "na"
                fid = _uniq(f"{building_id}__{config.slugify(tw,'main')}__{config.slugify(fr,'na')}", used)
                row = {k: None for k in schema.TABLES["floor_plate"].llm_names}
                row.update(floor_plate_id=fid, building_id=building_id,
                           tower_code=plan.get("tower_code"), floor_range=fr,
                           floor_label=plan.get("floor_label"),   # do vision đọc, không gán cứng
                           units_per_floor=plan["units_on_floor"], efficiency_per_floor_pct=None,
                           units_per_elevator=None, floorplate_url=None)
                tables["floor_plate"].append(row)
                prov.add("floor_plate", fid, row, [
                    {"field": "units_per_floor", "source_file": plan["source_file"],
                     "snippet": "đếm từ ảnh mặt bằng tầng", "confidence": "low"}])
                warn.append(f"B4: units_per_floor lấy từ vision ({plan['source_file']}), confidence low")

    # ---- B5 handover_item --------------------------------------------------
    used = set()
    for rec in text_out["handover_item"]["records"]:
        code = rec.get("item_code") or config.slugify(rec.get("item_name") or "item")
        hid = _uniq(f"{building_id}__{config.slugify(code, 'item')}", used)
        row = {k: rec.get(k) for k in schema.TABLES["handover_item"].llm_names}
        row.update(handover_id=hid, building_id=building_id,
                   applies_to_unit_type_id=resolve_type(rec.get("applies_to_type_code")))
        tables["handover_item"].append(row)
        prov.add("handover_item", hid, row, rec.get("provenance"))

    # ---- B6 amenity --------------------------------------------------------
    used = set()
    for rec in text_out["amenity"]["records"]:
        slug = rec.get("slug") or config.slugify(rec.get("amenity_name") or "amenity")
        aid = _uniq(f"{building_id}__{config.slugify(slug, 'amenity')}", used)
        row = {k: rec.get(k) for k in schema.TABLES["amenity"].llm_names}
        row.update(amenity_id=aid, building_id=building_id)
        tables["amenity"].append(row)
        prov.add("amenity", aid, row, rec.get("provenance"))

    # ---- B7 price_obs ------------------------------------------------------
    used, today = set(), date.today().isoformat()
    for rec in text_out["price_obs"]["records"]:
        uid = resolve_type(rec.get("unit_type_code"))
        pid = _uniq(f"{building_id}__{uid or 'all'}__{rec.get('market') or 'na'}"
                    f"__{rec.get('period') or 'na'}", used)
        row = {k: rec.get(k) for k in schema.TABLES["price_obs"].llm_names}
        fx = config.FX_TO_USD.get(rec.get("currency") or "")
        avg = rec.get("price_avg")
        if avg is None and rec.get("price_min") is not None and rec.get("price_max") is not None:
            avg = (rec["price_min"] + rec["price_max"]) / 2
        usd = round(avg * fx, 2) if (fx and avg is not None and rec.get("price_unit") == "per_m2") else None
        row.update(price_id=pid, building_id=building_id, unit_type_id=uid, observed_at=today,
                   fx_rate_to_usd=fx, price_usd_per_m2=usd)
        tables["price_obs"].append(row)
        prov.add("price_obs", pid, row, rec.get("provenance"))

    # ---- B1 building + cột derived ----------------------------------------
    brecs = text_out["building"]["records"]
    if not brecs:
        raise SystemExit("Không trích được record B1 nào — dừng.")
    if len(brecs) > 1:
        warn.append(f"B1: model trả {len(brecs)} record, chỉ giữ record đầu")
    b = brecs[0]
    row = {k: b.get(k) for k in schema.TABLES["building"].llm_names}
    row.update(building_id=building_id, building_name=resolved["building_name"],
               country=resolved["country"], city=resolved["city"],
               official_website=resolved.get("official_website"),
               linked_case_id=linked_case_id, is_target=is_target)
    row["building_name_local"] = row.get("building_name_local") or resolved.get("building_name_local")
    row["project_name"] = row.get("project_name") or resolved.get("project_name")
    row["developer"] = row.get("developer") or resolved.get("developer")
    row.update(_derive_b1(tables, row))
    tables["building"] = [row]
    prov.add("building", building_id, row, b.get("provenance"))

    bench = [_benchmark(row, tables, manifest)]
    return tables, prov.rows, warn, bench


def _derive_b1(t: Dict[str, Rows], b_row: Dict[str, Any]) -> Dict[str, Any]:
    uts, fps = t["unit_type"], t["floor_plate"]
    areas = [r["area_net_m2"] for r in uts if r.get("area_net_m2") is not None] or \
            [r["area_gross_m2"] for r in uts if r.get("area_gross_m2") is not None]
    classes = [r["layout_class"] for r in uts if r.get("layout_class")]

    weight: Counter = Counter()
    for r in uts:
        if r.get("layout_class"):
            weight[r["layout_class"]] += r.get("num_units_of_type") or r.get("share_of_total_pct") or 1

    bed: Dict[str, float] = defaultdict(float)
    for r in uts:
        if r.get("bedrooms") is not None:
            bed[str(r["bedrooms"])] += r.get("num_units_of_type") or r.get("share_of_total_pct") or 0
    total_bed = sum(bed.values())
    mix = ({k: round(v / total_bed * 100, 1) for k, v in sorted(bed.items())} if total_bed else None)

    typical = [r["units_per_floor"] for r in fps
               if r.get("floor_label") == "dien_hinh" and r.get("units_per_floor")]

    return {
        "efficiency_ratio_pct": _pct(b_row.get("nfa_sale_m2"), b_row.get("gfa_m2")),
        "unit_types_summary": sorted(set(classes)) or None,
        "unit_area_min_m2": min(areas) if areas else None,
        "unit_area_max_m2": max(areas) if areas else None,
        "units_per_floor_typical": max(typical) if typical else None,
        "dominant_layout_class": weight.most_common(1)[0][0] if weight else None,
        "mix_by_bedroom_pct": mix,
        "handover_brands": sorted({r["brand"] for r in t["handover_item"] if r.get("brand")}) or None,
        "amenity_count": len(t["amenity"]),
        "amenity_highlights": [r["amenity_name"] for r in t["amenity"] if r.get("is_highlight")][:8] or None,
    }


def _benchmark(b: Dict[str, Any], t: Dict[str, Rows], manifest: Dict[str, Any]) -> Dict[str, Any]:
    def usd(market: str) -> Optional[float]:
        vals = [r["price_usd_per_m2"] for r in t["price_obs"]
                if r.get("market") == market and r.get("price_usd_per_m2") is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    p1, p2 = usd("so_cap"), usd("thu_cap")
    # §8.1: chỉ so sánh khi cùng price_basis và cùng includes_vat
    bases = {(r.get("price_basis"), r.get("includes_vat")) for r in t["price_obs"]
             if r.get("market") in ("so_cap", "thu_cap") and r.get("price_usd_per_m2") is not None}
    premium = round((p2 / p1 - 1) * 100, 1) if (p1 and p2 and len(bases) == 1) else None

    yoy = None
    per: Dict[str, List[float]] = defaultdict(list)
    for r in t["price_obs"]:
        if r.get("market") == "so_cap" and r.get("price_usd_per_m2") and r.get("period"):
            per[str(r["period"])[:4]].append(r["price_usd_per_m2"])
    years = sorted(per)
    if len(years) >= 2 and int(years[-1]) - int(years[-2]) == 1:
        a = sum(per[years[-2]]) / len(per[years[-2]])
        z = sum(per[years[-1]]) / len(per[years[-1]])
        yoy = round((z / a - 1) * 100, 1) if a else None

    return {**b,
            "num_unit_types": len(t["unit_type"]), "num_rooms_captured": len(t["unit_room"]),
            "num_handover_items": len(t["handover_item"]), "num_amenities": len(t["amenity"]),
            "num_price_obs": len(t["price_obs"]),
            "price_usd_per_m2_primary": p1, "price_usd_per_m2_secondary": p2,
            "secondary_premium_pct": premium, "price_growth_pct_yoy": yoy,
            "sources_ok": manifest.get("ok", 0), "extracted_at": date.today().isoformat()}
