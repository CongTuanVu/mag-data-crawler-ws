#!/usr/bin/env python3
"""Build a self-contained HTML report from the WS2 building CSVs in output_csv/.

Đọc mọi *.csv trong output_csv/ (định dạng long: mỗi dòng = 1 bản ghi của một
bảng B1..B7, phân biệt qua cột `bang`), gom theo toà nhà rồi nhúng JSON vào
template.html để tạo ra dist/index.html — mở trực tiếp bằng trình duyệt.

    python3 code_ui/build_html.py
    python3 code_ui/build_html.py --csv-dir output_csv --out code_ui/dist/index.html
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent

# Cột thuộc về từng bảng — chỉ giữ các cột này khi xuất JSON để file gọn.
FIELDS: dict[str, list[str]] = {
    "building": [
        "building_id", "building_name", "building_name_local", "project_name",
        "tower_codes", "linked_case_id", "is_target", "country", "city",
        "district", "address", "latitude", "longitude", "developer",
        "official_website", "brochure_url", "building_type", "segment",
        "status", "year_launch", "year_handover", "num_towers",
        "num_floors_above", "num_basements", "height_m", "num_units_total",
        "land_area_m2", "gfa_m2", "nfa_sale_m2", "efficiency_ratio_pct",
        "building_density_pct", "green_area_pct", "parking_ratio",
        "unit_types_summary", "unit_area_min_m2", "unit_area_max_m2",
        "units_per_floor_typical", "dominant_layout_class", "mix_by_bedroom_pct",
        "handover_standard", "handover_brands", "amenity_count",
        "amenity_highlights", "architect_firm", "architect_country",
        "interior_designer", "landscape_architect", "architectural_style",
        "design_concept", "design_concept_keywords", "massing_form",
        "facade_material", "facade_system", "window_wall_ratio_pct",
        "balcony_type", "floor_to_ceiling_m", "signature_features",
        "green_cert", "green_cert_level", "awards", "orientation_note",
        "num_unit_types", "num_rooms_captured", "num_handover_items",
        "num_amenities", "num_price_obs", "price_usd_per_m2_primary",
        "price_usd_per_m2_secondary", "secondary_premium_pct",
        "price_growth_pct_yoy", "sources_ok", "extracted_at", "confidence",
        "source_urls",
    ],
    "unit_type": [
        "unit_type_id", "type_code", "type_name", "layout_class", "bedrooms",
        "bathrooms", "has_multipurpose_room", "area_gross_m2", "area_net_m2",
        "area_basis_reported", "ratio_net_gross_pct", "area_balcony_m2",
        "num_units_of_type", "share_of_total_pct", "facing", "view_type",
        "is_corner", "floorplan_url", "confidence", "source_urls",
    ],
    "unit_room": [
        "room_id", "unit_type_id", "room_code", "room_type", "room_label_raw",
        "area_m2", "width_m", "length_m", "has_window", "is_ensuite",
        "position_note", "confidence", "source_urls",
    ],
    "floor_plate": [
        "floor_plate_id", "tower_code", "floor_range", "floor_label",
        "units_per_floor", "unit_type_mix", "gfa_per_floor_m2",
        "nfa_per_floor_m2", "efficiency_per_floor_pct", "corridor_type",
        "num_elevators", "num_elevators_service", "units_per_elevator",
        "num_stairs", "core_position", "floorplate_url", "confidence",
        "source_urls",
    ],
    "handover_item": [
        "handover_id", "applies_to_unit_type_id", "item_code", "item_category",
        "item_name", "item_spec", "brand", "brand_origin", "is_included",
        "note", "confidence", "source_urls",
    ],
    "amenity": [
        "amenity_id", "amenity_category", "amenity_name", "amenity_name_local",
        "location", "floor_level", "is_indoor", "is_resident_free",
        "operator_brand", "is_highlight", "confidence", "source_urls",
    ],
    "price_obs": [
        "price_id", "unit_type_id", "market", "price_min", "price_max",
        "price_avg", "currency", "price_unit", "price_basis", "includes_vat",
        "includes_maintenance_fee", "period", "observed_at", "sample_size",
        "fx_rate_to_usd", "price_usd_per_m2", "listing_url", "source_type",
        "confidence", "source_urls",
    ],
}

# Bảng con → key trong JSON của toà nhà.
CHILD_TABLES = {
    "unit_type": "unit_types",
    "unit_room": "rooms",
    "floor_plate": "floor_plates",
    "handover_item": "handover_items",
    "amenity": "amenities",
    "price_obs": "prices",
}

NUMERIC_HINTS = (
    "_m2", "_pct", "_m", "num_", "year_", "price_", "latitude", "longitude",
    "height_m", "bedrooms", "bathrooms", "sample_size", "fx_rate_to_usd",
    "parking_ratio", "units_per_floor", "sources_ok", "floor_level",
)
BOOL_FIELDS = {
    "is_target", "is_corner", "has_multipurpose_room", "has_window",
    "is_ensuite", "is_included", "is_indoor", "is_resident_free",
    "is_highlight", "includes_vat", "includes_maintenance_fee",
}


def coerce(field: str, raw: str):
    """Ép kiểu ô CSV về bool/number/string cho gọn phía JS."""
    val = (raw or "").strip()
    if not val:
        return None
    if field in BOOL_FIELDS:
        low = val.lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
        return val
    if any(h in field for h in NUMERIC_HINTS):
        try:
            num = float(val)
        except ValueError:
            return val
        return int(num) if num.is_integer() else num
    return val


def pick(row: dict, table: str) -> dict:
    out = {}
    for field in FIELDS.get(table, []):
        if field not in row:
            continue
        value = coerce(field, row[field])
        if value is not None:
            out[field] = value
    return out


def load_csv(path: Path) -> dict | None:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return None

    building = None
    children: dict[str, list] = {key: [] for key in CHILD_TABLES.values()}

    for row in rows:
        table = (row.get("bang_ten") or "").strip()
        if table == "building":
            building = pick(row, "building")
        elif table in CHILD_TABLES:
            record = pick(row, table)
            if record:
                record["_label"] = (row.get("record_label") or "").strip()
                children[CHILD_TABLES[table]].append(record)

    if building is None:
        print(f"  ! bỏ qua {path.name}: không có dòng B1 building", file=sys.stderr)
        return None

    building.setdefault("building_id", path.stem)
    building.setdefault("building_name", path.stem)
    building["_file"] = path.name
    building.update(children)
    return building


def build(csv_dir: Path, template: Path, out: Path) -> None:
    files = sorted(p for p in csv_dir.glob("*.csv"))
    if not files:
        raise SystemExit(f"Không tìm thấy CSV nào trong {csv_dir}")

    buildings = []
    for path in files:
        print(f"  · đọc {path.name}")
        data = load_csv(path)
        if data:
            buildings.append(data)

    if not buildings:
        raise SystemExit("Không dựng được toà nhà nào từ CSV.")

    buildings.sort(key=lambda b: (b.get("building_name") or "").lower())

    payload = {
        "generated_at": max(
            (b.get("extracted_at") or "") for b in buildings
        ) or "",
        "source_dir": str(csv_dir.relative_to(ROOT)) if csv_dir.is_relative_to(ROOT) else str(csv_dir),
        "buildings": buildings,
    }

    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", "<\\/")  # an toàn khi nhúng trong <script>

    html = template.read_text(encoding="utf-8")
    if "__DATA__" not in html:
        raise SystemExit(f"Template {template} thiếu placeholder __DATA__")
    html = html.replace("__DATA__", blob)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    size_kb = out.stat().st_size / 1024
    print(f"\n✓ {len(buildings)} toà nhà → {out}  ({size_kb:.0f} KB)")
    for b in buildings:
        print(
            f"    - {b.get('building_name')}: "
            f"{len(b.get('unit_types', []))} loại căn, "
            f"{len(b.get('handover_items', []))} hạng mục BG, "
            f"{len(b.get('amenities', []))} tiện ích, "
            f"{len(b.get('prices', []))} quan sát giá"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv-dir", default=str(ROOT / "output_csv"), type=Path)
    ap.add_argument("--template", default=str(HERE / "template.html"), type=Path)
    ap.add_argument("--out", default=str(HERE / "dist" / "index.html"), type=Path)
    args = ap.parse_args()

    build(args.csv_dir.resolve(), args.template.resolve(), args.out.resolve())


if __name__ == "__main__":
    main()
