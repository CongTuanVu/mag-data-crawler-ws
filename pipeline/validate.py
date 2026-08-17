"""Kiểm tra chéo — feature_spec §4.1 và §13.9.

Nguyên tắc: CHỈ log cảnh báo, KHÔNG tự sửa số. Một cảnh báo không làm hỏng lần
chạy; nó là tín hiệu để người đọc kiểm lại nguồn.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import schema

Rows = List[Dict[str, Any]]
BED_RE = re.compile(r"^phong_ngu_")


def _floors_in_range(rng: Optional[str]) -> Optional[int]:
    if not rng:
        return None
    m = re.match(r"\s*(\d+)\s*[-–~]\s*(\d+)\s*$", str(rng))
    if m:
        return int(m.group(2)) - int(m.group(1)) + 1
    return 1 if re.match(r"^\s*\d+\s*$", str(rng)) else None


def check(tables: Dict[str, Rows], provenance: Rows) -> List[str]:
    w: List[str] = []
    b = tables["building"][0] if tables["building"] else {}

    # ── khoá: không rỗng, không trùng ───────────────────────────────────────
    for name, key in (("building", "building_id"), ("unit_type", "unit_type_id"),
                      ("unit_room", "room_id"), ("floor_plate", "floor_plate_id"),
                      ("handover_item", "handover_id"), ("amenity", "amenity_id"),
                      ("price_obs", "price_id")):
        keys = [r.get(key) for r in tables[name]]
        if any(not k for k in keys):
            w.append(f"[KEY] {name}: có dòng khoá rỗng")
        if len(keys) != len(set(keys)):
            w.append(f"[KEY] {name}: khoá trùng ({len(keys) - len(set(keys))} dòng)")

    # ── §4.1 diện tích phòng vs diện tích căn ───────────────────────────────
    by_type: Dict[str, Rows] = {}
    for r in tables["unit_room"]:
        by_type.setdefault(r["unit_type_id"], []).append(r)
    for ut in tables["unit_type"]:
        rooms = by_type.get(ut["unit_type_id"], [])
        if not rooms:
            continue
        s = sum(r["area_m2"] for r in rooms if r.get("area_m2") is not None)
        gross = ut.get("area_gross_m2")
        if s and gross:
            if s > gross:
                w.append(f"[B3>B2] {ut['unit_type_id']}: tổng phòng {s:.1f} m² > "
                         f"area_gross_m2 {gross:.1f} m² — đặt confidence=low, KHÔNG sửa số")
            else:
                gap = (gross - s) / gross * 100
                if not (5 <= gap <= 15):
                    w.append(f"[B3 gap] {ut['unit_type_id']}: chênh tường+hộp kỹ thuật {gap:.1f}% "
                             f"(hợp lý 5–15%)")
        n_bed = sum(1 for r in rooms if BED_RE.match(r.get("room_type") or ""))
        if ut.get("bedrooms") is not None and n_bed and n_bed != ut["bedrooms"]:
            w.append(f"[B3 PN] {ut['unit_type_id']}: đếm {n_bed} phòng ngủ ≠ B2.bedrooms "
                     f"{ut['bedrooms']}")

    # ── §13.9 tổng số căn ───────────────────────────────────────────────────
    tot = b.get("num_units_total")
    s_units = sum(r["num_units_of_type"] for r in tables["unit_type"]
                  if r.get("num_units_of_type") is not None)
    if tot and s_units and abs(s_units - tot) / tot > 0.02:
        w.append(f"[B2 tổng] Σ num_units_of_type = {s_units} ≠ B1.num_units_total = {tot}")

    s_share = sum(r["share_of_total_pct"] for r in tables["unit_type"]
                  if r.get("share_of_total_pct") is not None)
    if s_share and abs(s_share - 100) > 5:
        w.append(f"[B2 share] Σ share_of_total_pct = {s_share:.1f}% ≠ 100%")

    est = 0
    for fp in tables["floor_plate"]:
        n = _floors_in_range(fp.get("floor_range"))
        if n and fp.get("units_per_floor"):
            est += n * fp["units_per_floor"]
    if tot and est and abs(est - tot) / tot > 0.15:
        w.append(f"[B4 tổng] Σ(units_per_floor × số tầng) ≈ {est} lệch >15% so với "
                 f"num_units_total = {tot}")

    # ── cơ sở diện tích / giá bắt buộc (§13.1, §13.3) ───────────────────────
    for r in tables["unit_type"]:
        if not r.get("area_basis_reported"):
            w.append(f"[BẮT BUỘC] {r['unit_type_id']}: thiếu area_basis_reported")
        if r.get("area_gross_m2") and r.get("area_net_m2") and \
                r["area_net_m2"] > r["area_gross_m2"]:
            w.append(f"[ĐẢO] {r['unit_type_id']}: thông thuỷ > tim tường")
    for r in tables["price_obs"]:
        for f in ("market", "currency", "price_unit", "price_basis", "period"):
            if not r.get(f):
                w.append(f"[BẮT BUỘC] {r['price_id']}: thiếu {f}")
        if all(r.get(k) is None for k in ("price_min", "price_max", "price_avg")):
            w.append(f"[RỖNG] {r['price_id']}: không có giá trị giá nào")

    # ── dải giá trị ─────────────────────────────────────────────────────────
    for f, lo, hi in (("latitude", -90, 90), ("longitude", -180, 180),
                      ("building_density_pct", 0, 100), ("green_area_pct", 0, 100),
                      ("efficiency_ratio_pct", 0, 100), ("window_wall_ratio_pct", 0, 100)):
        v = b.get(f)
        if v is not None and not (lo <= v <= hi):
            w.append(f"[DẢI] building.{f} = {v} ngoài [{lo}, {hi}]")

    # ── cơ sở diện tích lẫn lộn ở B1 §1.3 ───────────────────────────────────
    n_net = sum(1 for r in tables["unit_type"] if r.get("area_net_m2") is not None)
    if 0 < n_net < len(tables["unit_type"]):
        w.append(f"[B1 §1.3] unit_area_min/max_m2 chỉ tính trên {n_net}/{len(tables['unit_type'])} "
                 f"loại căn có area_net_m2 — không trộn với tim tường (spec §13.1)")

    # ── provenance phủ đến đâu (chỉ tính cột do LLM trích) ──────────────────
    have = {(p["table"], p["record_key"], p["field"]) for p in provenance}
    missing, filled = 0, 0
    for name, key in (("building", "building_id"), ("unit_type", "unit_type_id"),
                      ("floor_plate", "floor_plate_id"), ("handover_item", "handover_id"),
                      ("amenity", "amenity_id"), ("price_obs", "price_id")):
        for r in tables[name]:
            for f in schema.TABLES[name].llm_names:
                if r.get(f) in (None, "", [], {}):
                    continue
                filled += 1
                if (name, r[key], f) not in have:
                    missing += 1
    if missing:
        w.append(f"[PROV] {missing}/{filled} giá trị có dữ liệu nhưng không có evidence "
                 f"— không truy được nguồn, xem cột evidence_json")

    return w
