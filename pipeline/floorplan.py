"""Bước 3b — Đọc ảnh mặt bằng bằng vision để lấy B3 unit_room.

feature_spec §4.2: diện tích từng phòng hầu như chỉ tồn tại dưới dạng ảnh bản vẽ,
extractor deterministic không đọc được. Ở đây Claude vision đọc ảnh, nhưng mọi
dòng sinh ra đều `source_type = floorplan_image` và `confidence = low` cho tới khi
có người xác nhận.

Vòng người xác nhận: mỗi lần chạy ghi output_raw/<b>/refer_file/<b>_rooms.csv với
cột `verified` để trống. Điền `yes` (và sửa số nếu cần) rồi chạy lại — dòng đã
xác nhận sẽ được dùng thay cho kết quả vision, confidence nâng lên `high`.
"""
from __future__ import annotations

import base64
import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image

from . import config, llm, schema

MAX_EDGE = 2000          # px — giữ đủ nét để đọc số trên bản vẽ
MIN_EDGE = 500           # bỏ qua ảnh quá nhỏ, chắc chắn không phải bản vẽ

ROOM_FIELDS = ["room_code", "room_type", "room_label_raw", "area_m2", "width_m", "length_m",
               "has_window", "is_ensuite", "position_note"]

_room_props = {f.name: {"anyOf": [
    ({"type": "string", "enum": schema.V["room_type"]} if f.typ.startswith("enum:") else
     {"str": {"type": "string"}, "float": {"type": "number"},
      "int": {"type": "integer"}, "bool": {"type": "boolean"}}[f.typ]),
    {"type": "null"}], "description": f.desc}
    for f in schema.TABLES["unit_room"].llm if f.name in ROOM_FIELDS}
_room_props["label_on_drawing"] = {"type": "string",
                                   "description": "Chuỗi nguyên văn đọc được trên bản vẽ, "
                                                  "gồm cả đơn vị gốc trước khi quy đổi"}

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_floorplan": {"type": "boolean", "description": "Ảnh này có phải bản vẽ mặt bằng không"},
        "plan_kind": {"type": "string", "enum": ["unit", "floor", "site", "other"],
                      "description": "unit = mặt bằng 1 căn, floor = mặt bằng tầng điển hình"},
        "matched_type_code": {"anyOf": [{"type": "string"}, {"type": "null"}],
                              "description": "type_code trong danh sách loại căn đã biết mà bản vẽ "
                                             "này ứng với; không khớp chắc chắn → null"},
        "unit_label_raw": {"anyOf": [{"type": "string"}, {"type": "null"}],
                           "description": "Nhãn loại căn in trên bản vẽ, vd 'TYPE A2 84㎡'"},
        "total_area_on_plan": {"anyOf": [{"type": "number"}, {"type": "null"}],
                               "description": "Tổng diện tích căn ghi trên bản vẽ (m², đã quy đổi)"},
        "tower_code": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "floor_range": {"anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "Chỉ khi plan_kind = floor, vd '5-20'"},
        "floor_label": {"anyOf": [{"type": "string", "enum": schema.V["floor_label"]},
                                  {"type": "null"}],
                        "description": "Chỉ khi plan_kind = floor: loại sàn theo §8.13. "
                                       "Bản vẽ không nói rõ là sàn điển hình → null"},
        "units_on_floor": {"anyOf": [{"type": "integer"}, {"type": "null"}],
                           "description": "Chỉ khi plan_kind = floor: đếm số căn trên sàn"},
        "rooms": {"type": "array",
                  "items": {"type": "object", "properties": _room_props,
                            "required": list(_room_props), "additionalProperties": False}},
        "notes": {"type": "string"},
    },
    "required": ["is_floorplan", "plan_kind", "matched_type_code", "unit_label_raw",
                 "total_area_on_plan", "tower_code", "floor_range", "floor_label",
                 "units_on_floor", "rooms", "notes"],
    "additionalProperties": False,
}

SYSTEM = """\
Bạn đọc bản vẽ mặt bằng căn hộ để lấy diện tích từng phòng. Quy tắc:

1. Chỉ ghi con số IN TRÊN BẢN VẼ. Cấm ước lượng diện tích từ tỷ lệ hình vẽ, cấm
   suy diện tích phòng từ tổng diện tích căn. Không có số → area_m2 = null nhưng
   vẫn giữ dòng phòng đó (room_type + room_label_raw).
2. Quy đổi ngay: 帖/畳 ×1.62, 평/坪 ×3.30579, sq ft ×0.092903. `label_on_drawing`
   giữ nguyên văn số gốc kèm đơn vị.
3. room_type dùng đúng danh mục §8.11. Nhãn bản địa: 안방/主寝室 → phong_ngu_master,
   洋室/침실 → phong_ngu_*, LDK/거실 → phong_khach, 주방/キッチン/厨房 → bep,
   욕실/浴室 → wc_chung, 발코니/バルコニー/阳台 → ban_cong, 팬트리/納戸 → kho.
4. Đánh số phòng ngủ theo thứ tự diện tích giảm dần: lớn nhất = phong_ngu_master.
5. Ảnh không phải bản vẽ mặt bằng (phối cảnh, ảnh chụp, bìa brochure, sơ đồ vị trí)
   → is_floorplan = false, rooms rỗng. Đừng cố đoán.
"""


def _prepare(path: Path) -> tuple:
    """Trả (base64, media_type) hoặc (None, None) nếu ảnh không dùng được."""
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return None, None
    if max(img.size) < MIN_EDGE:
        return None, None
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > MAX_EDGE:
        r = MAX_EDGE / max(img.size)
        img = img.resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode(), "image/png"


def _unit_type_hint(unit_types: List[Dict[str, Any]]) -> str:
    if not unit_types:
        return "Chưa biết loại căn nào — để matched_type_code = null."
    lines = [f"- {r.get('type_code')} | {r.get('layout_class')} | {r.get('bedrooms')}PN | "
             f"gross={r.get('area_gross_m2')} net={r.get('area_net_m2')}" for r in unit_types]
    return "Các loại căn đã trích được từ text (khớp bản vẽ vào đây nếu đúng):\n" + "\n".join(lines)


def run(out_dir: Path, unit_types: List[Dict[str, Any]], limit: int = 20) -> Dict[str, Any]:
    fp_dir = out_dir / "floorplans"
    images = sorted([p for p in fp_dir.glob("*") if p.suffix.lower() in
                     {".png", ".jpg", ".jpeg", ".webp"}]) if fp_dir.exists() else []
    print(f"[3b] Đọc mặt bằng bằng vision: {len(images)} ảnh (đọc tối đa {limit})")
    if not images:
        return {"plans": []}

    system = [{"type": "text", "text": SYSTEM},
              llm.cached(_unit_type_hint(unit_types))]
    plans: List[Dict[str, Any]] = []
    for path in images[:limit]:
        b64, media = _prepare(path)
        if not b64:
            continue
        res = llm.call_json(
            system=system,
            user_content=[llm.image_block(b64, media),
                          {"type": "text", "text": f"Ảnh: floorplans/{path.name}\n"
                                                   "Đọc bản vẽ này theo quy tắc trên."}],
            schema=SCHEMA, max_tokens=12000, label=f"vision:{path.name[:34]}")
        res["source_file"] = f"floorplans/{path.name}"
        plans.append(res)
        if res["is_floorplan"]:
            print(f"      {path.name}: {res['plan_kind']} · type={res['matched_type_code']} · "
                  f"{len(res['rooms'])} phòng")
    out = {"plans": plans}
    (out_dir / "extract_floorplan.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ── Vòng người xác nhận ──────────────────────────────────────────────────────
REFER_COLS = ["verified", "type_code", "room_code", "room_type", "room_label_raw", "area_m2",
              "width_m", "length_m", "has_window", "is_ensuite", "position_note",
              "source_file", "label_on_drawing"]


def refer_path(out_dir: Path, building_id: str) -> Path:
    return out_dir / "refer_file" / f"{building_id}_rooms.csv"


def load_verified(out_dir: Path, building_id: str) -> List[Dict[str, Any]]:
    p = refer_path(out_dir, building_id)
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if (r.get("verified") or "").strip().lower() in {"yes", "y", "1", "true"}]
    if rows:
        print(f"      · dùng {len(rows)} dòng phòng ĐÃ XÁC NHẬN trong {p.name}")
    return rows


def write_refer(out_dir: Path, building_id: str, rows: List[Dict[str, Any]]) -> Path:
    p = refer_path(out_dir, building_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():                      # không đè công sức người dùng đã nhập
        p = p.with_name(p.stem + "_new.csv")
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REFER_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**{c: "" for c in REFER_COLS}, **r})
    return p
