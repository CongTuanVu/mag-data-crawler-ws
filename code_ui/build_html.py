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
import re
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


def _building_key(row: dict, fallback: str) -> str:
    """Toà nào sở hữu dòng này.

    File `thread<N>_<ngày>.csv` chứa NHIỀU toà nên không thể lấy tên file làm khoá
    nữa. `record_key` có dạng `<building_id>__<gì đó>` nên vẫn nhận ra chủ của
    dòng B3 unit_room — bảng duy nhất không có cột building_id.
    """
    bid = (row.get("building_id") or "").strip()
    if bid:
        return bid
    key = (row.get("record_key") or "").strip()
    return key.split("__")[0] if "__" in key else fallback


def load_csv(path: Path) -> list[dict]:
    """Đọc một CSV → danh sách toà. Nhận cả file 1 toà lẫn file gộp nhiều toà."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return []

    order: list[str] = []
    found: dict[str, dict] = {}
    kids: dict[str, dict[str, list]] = {}

    for row in rows:
        bid = _building_key(row, path.stem)
        if bid not in kids:
            order.append(bid)
            kids[bid] = {key: [] for key in CHILD_TABLES.values()}
        table = (row.get("bang_ten") or "").strip()
        if table == "building":
            found[bid] = pick(row, "building")
        elif table in CHILD_TABLES:
            record = pick(row, table)
            if record:
                record["_label"] = (row.get("record_label") or "").strip()
                kids[bid][CHILD_TABLES[table]].append(record)

    out = []
    for bid in order:
        building = found.get(bid)
        if building is None:
            print(f"  ! bỏ qua {bid} trong {path.name}: không có dòng B1 building",
                  file=sys.stderr)
            continue
        building.setdefault("building_id", bid)
        building.setdefault("building_name", bid)
        building["_file"] = path.name
        building.update(kids[bid])
        out.append(building)
    return out


SIGNATURE = ("bang", "record_key")


def is_data_csv(path: Path) -> bool:
    """CSV đúng định dạng long của pipeline, không phải file của công cụ khác."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            header = next(csv.reader(fh), [])
    except OSError:
        return False
    return all(col in header for col in SIGNATURE)


def pick_files(csv_dir: Path, every: bool = False) -> list[Path]:
    """File nào để dựng UI.

    Mặc định: có file `thread<N>_<dấu thời gian>.csv` thì chỉ đọc MẺ MỚI NHẤT —
    chúng đã chứa mọi toà, đọc thêm nữa là mỗi toà hiện lên hai lần. Chưa gộp thì
    đọc file từng toà.

    `every=True` (--all): đọc MỌI CSV đúng định dạng trong thư mục — cả mẻ gộp cũ
    lẫn CSV từng toà. Trùng lặp là chắc chắn xảy ra, nên `build()` khử trùng theo
    building_id, bản ở file sửa gần đây nhất thắng.

    `_benchmark.csv` luôn bỏ qua (bản rút gọn của cùng dữ liệu); file lạ định dạng
    (vd file_lan.csv) bị loại bằng kiểm tra cột đặc trưng.
    """
    if every:
        return sorted(p for p in csv_dir.glob("*.csv")
                      if not p.name.startswith("_") and is_data_csv(p))
    stamps = {}
    for p in csv_dir.glob("thread*_*.csv"):
        # `thread3_20260818_143052.csv` → `20260818_143052`; đời cũ chỉ có ngày.
        m = re.fullmatch(r"thread\d+_(\d{8}(?:_\d{6})?)\.csv", p.name)
        if m:
            stamps.setdefault(m.group(1), []).append(p)
    if stamps:
        # Dấu thời gian sắp xếp theo chuỗi là đúng thứ tự thời gian. Bản chỉ có
        # ngày xếp TRƯỚC mọi bản cùng ngày có giờ, đúng ý: nó là bản cũ hơn.
        return sorted(stamps[max(stamps)])
    return sorted(p for p in csv_dir.glob("*.csv")
                  if not p.name.startswith("_") and is_data_csv(p))


def richness(b: dict) -> int:
    """Số ô CÓ DỮ LIỆU của một toà, tính cả bảng con.

    Dùng để chọn giữa các bản trùng. Cùng một toà có thể được trích nhiều lần với
    độ phủ khác nhau (đổi rules.py, thêm nguồn crawl, dịch xong thuật ngữ) — bản
    ghi sau CHƯA CHẮC đầy đủ hơn, nên đếm dữ liệu chứ đừng tin dấu thời gian.
    """
    score = 0
    for key, val in b.items():
        if key.startswith("_"):
            continue
        if isinstance(val, list):                      # unit_types, amenities…
            score += sum(1 for rec in val for k, v in rec.items()
                         if not k.startswith("_") and v not in (None, ""))
        elif val not in (None, ""):
            score += 1
    return score


def name_key(b: dict) -> str:
    """Khoá gộp theo TÊN toà, chuẩn hoá nhẹ (thường hoá + gộp khoảng trắng).

    Chỉ khớp tên GIỐNG HỆT sau chuẩn hoá — không cắt hậu tố, không so gần đúng.
    "The Kosugi Tower" và "The Kosugi Tower Musashikosugi" phải nằm riêng, vì
    không có cách nào biết chắc chúng là một mà không đọc dữ liệu.
    """
    return re.sub(r"\s+", " ", (b.get("building_name") or "").strip().lower()).rstrip(" .")


def build(csv_dir: Path, template: Path, out: Path, every: bool = False,
          merge_names: bool = True) -> None:
    files = pick_files(csv_dir, every)
    if not files:
        raise SystemExit(f"Không tìm thấy CSV nào trong {csv_dir}")

    # building_id → (điểm xếp hạng, dữ liệu toà). Cùng một toà nằm ở nhiều file là
    # chuyện bình thường (CSV riêng + các mẻ gộp), nên chọn BẢN ĐẦY ĐỦ NHẤT thay
    # vì để nó hiện lên nhiều lần trong UI. Hoà điểm mới xét tới file mới hơn.
    best: dict[str, tuple[tuple[int, float], dict]] = {}
    dups = won_by_data = 0
    for path in files:
        mtime = path.stat().st_mtime
        found = load_csv(path)
        added = 0
        for b in found:
            bid = b["building_id"]
            rank = (richness(b), mtime)
            old = best.get(bid)
            if old is None:
                added += 1
            else:
                dups += 1
                if old[0] >= rank:
                    continue
                if rank[0] > old[0][0]:
                    won_by_data += 1
            best[bid] = (rank, b)
        note = f" ({added} mới)" if every and added != len(found) else ""
        print(f"  · đọc {path.name} → {len(found)} toà{note}")

    buildings = [b for _, b in best.values()]
    if not buildings:
        raise SystemExit("Không dựng được toà nhà nào từ CSV.")
    if dups:
        extra = f", {won_by_data} lần bản đầy hơn thắng bản mới hơn" if won_by_data else ""
        print(f"  · khử {dups} bản trùng → giữ bản nhiều dữ liệu nhất{extra}")

    # ── Gộp lần hai: cùng TÊN nhưng khác building_id ────────────────────────
    # buildings.txt có những dòng chỉ cùng một toà viết khác đi ("Asakusa Tower"
    # và "Asakusa Tower, Taito"), bước tìm nguồn sinh ra hai slug khác nhau, và
    # UI hiện lên hai lần. Khử theo id không bắt được, phải khử theo tên.
    if merge_names:
        by_name: dict[str, dict] = {}
        collapsed: list[tuple[dict, dict]] = []
        for b in buildings:
            key = name_key(b) or f"\x00{b['building_id']}"     # không tên → để riêng
            prev = by_name.get(key)
            if prev is None:
                by_name[key] = b
                continue
            keep, drop = ((b, prev) if richness(b) > richness(prev) else (prev, b))
            by_name[key] = keep
            collapsed.append((keep, drop))
        buildings = list(by_name.values())
        if collapsed:
            print(f"  · gộp {len(collapsed)} toà trùng TÊN (khác building_id) "
                  f"→ giữ bản nhiều dữ liệu nhất:")
            for keep, drop in sorted(collapsed, key=lambda kd: kd[0]["building_name"]):
                print(f"      {keep['building_name']}: giữ {keep['building_id']} "
                      f"({richness(keep)} ô) · bỏ {drop['building_id']} ({richness(drop)} ô)")

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
    ap.add_argument("--all", dest="every", action="store_true",
                    help="Đọc MỌI CSV trong thư mục (cả mẻ gộp cũ lẫn CSV từng toà), "
                         "khử trùng theo building_id")
    ap.add_argument("--keep-dupes", dest="merge_names", action="store_false",
                    help="Giữ nguyên các toà trùng tên nhưng khác building_id "
                         "(mặc định gộp, giữ bản nhiều dữ liệu nhất)")
    args = ap.parse_args()

    build(args.csv_dir.resolve(), args.template.resolve(), args.out.resolve(),
          args.every, args.merge_names)


if __name__ == "__main__":
    main()
