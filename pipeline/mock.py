"""Chế độ offline — giả lập mọi lệnh gọi model để test local khi chưa có API key.

Sinh record giả BÁM ĐÚNG JSON Schema thật của từng bảng, nên mọi bước phía sau
(sinh khoá, tính cột derived, kiểm tra chéo, ghi CSV) chạy y như thật. Chỉ phần
*phán đoán* là giả; crawl vẫn ra mạng thật, evidence vẫn trỏ vào file raw thật.

    python3 run.py "Toà nào đó" --offline --sources sources_demo.txt

Giá trị sinh ra là **deterministic** (theo tên trường), chạy lại cho kết quả y hệt
— để kiểm tra tính idempotent của pipeline (feature_spec §13 quy tắc 6).

⚠️ Số liệu trong CSV chế độ này là RÁC. Dùng để kiểm tra đường ống, không phải dữ liệu.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

FILE_RE = re.compile(r"=== FILE: ([^\s|]+)")
TYPE_RE = re.compile(r"^- (.+?) \| ", re.M)      # dòng gợi ý loại căn: "- 2PN-A | 2pn | …"

RECORDS_PER_TABLE = {"building": 1, "unit_type": 2, "floor_plate": 1,
                     "handover_item": 3, "amenity": 3, "price_obs": 2}


def _n(field: str, lo: float, hi: float) -> float:
    """Số ổn định theo tên trường, nằm trong [lo, hi]."""
    h = sum(ord(c) * (i + 1) for i, c in enumerate(field))
    return round(lo + (h % 1000) / 1000 * (hi - lo), 1)


def _value(field: str, leaf: Dict[str, Any], idx: int) -> Any:
    t = leaf.get("type")
    if "enum" in leaf:
        return leaf["enum"][idx % len(leaf["enum"])]
    if t == "string":
        return f"MOCK {field} #{idx + 1}"
    if t == "integer":
        return int(_n(field, 1, 40))
    if t == "number":
        return _n(field, 10, 120)
    if t == "boolean":
        return sum(ord(c) for c in field) % 2 == 0
    if t == "array":
        item = leaf.get("items", {})
        if item.get("type") == "object":                       # dạng pairs {key,value}
            return [{"key": "2pn", "value": 4}, {"key": "3pn", "value": 2}]
        return [f"MOCK {field} A", f"MOCK {field} B"]
    return None


def _record(props: Dict[str, Any], idx: int, files: List[str]) -> Dict[str, Any]:
    rec: Dict[str, Any] = {}
    prov: List[Dict[str, Any]] = []
    src = files[idx % len(files)] if files else "mock_source.txt"
    for name, spec in props.items():
        if name == "provenance":
            continue
        leaf = next((o for o in spec.get("anyOf", []) if o.get("type") != "null"), spec)
        # cố tình để trống ~1/5 số trường, giống nguồn thật luôn thiếu dữ liệu
        if sum(ord(c) for c in name) % 5 == 0:
            rec[name] = None
            continue
        rec[name] = _value(name, leaf, idx)
        prov.append({"field": name, "source_file": src,
                     "snippet": f"[mock] câu nguồn chứa {name}",
                     "confidence": ("high", "medium", "low")[idx % 3]})
    if "provenance" in props:
        rec["provenance"] = prov
    return rec


def call_json(system: List[Dict[str, Any]], user_content: Any, schema: Dict[str, Any],
              tools: Any = None, max_tokens: int = 0, effort: str = "", label: str = "") -> Dict[str, Any]:
    sys_text = "\n".join(b.get("text", "") for b in system if isinstance(b, dict))
    files = FILE_RE.findall(sys_text)
    print(f"    · {label}: [OFFLINE] sinh dữ liệu giả từ {len(files)} file raw")

    props = schema.get("properties", {})

    if "resolved" in props:                                    # discover
        q = user_content if isinstance(user_content, str) else "Mock Building"
        name = q.split("\n")[0].replace("Toà nhà cần khảo sát:", "").strip() or "Mock Building"
        return {"resolved": {"found": True, "building_name": name,
                             "building_name_local": None, "project_name": None,
                             "country": "MOCK", "city": "MOCK",
                             "official_website": None, "developer": "MOCK developer",
                             "building_id_suggestion": name.lower(),
                             "disambiguation_note": "[offline] không tìm kiếm thật",
                             "search_languages": ["mock"]},
                "sources": [], "gaps": "[offline] cần --sources để có nguồn thật"}

    if "is_floorplan" in props:                                # vision
        codes = TYPE_RE.findall(sys_text)
        rooms = [{"room_code": c, "room_type": t, "room_label_raw": f"MOCK {t}",
                  "area_m2": _n(t, 8, 24), "width_m": None, "length_m": None,
                  "has_window": True, "is_ensuite": False, "position_note": None,
                  "label_on_drawing": f"[mock] {t} {_n(t, 8, 24)}"}
                 for c, t in (("pk", "phong_khach"), ("pn1", "phong_ngu_master"),
                              ("pn2", "phong_ngu_2"), ("bep", "bep"))]
        return {"is_floorplan": True, "plan_kind": "unit",
                "matched_type_code": codes[0] if codes else None,
                "unit_label_raw": "[mock] TYPE A", "total_area_on_plan": 75.0,
                "tower_code": None, "floor_range": None, "floor_label": None,
                "units_on_floor": None, "rooms": rooms, "notes": "[offline] dữ liệu giả"}

    table = label.split(":")[-1]                               # extract:<tên bảng>
    item_props = props["records"]["items"]["properties"]
    n = RECORDS_PER_TABLE.get(table, 2)
    return {"records": [_record(item_props, i, files) for i in range(n)],
            "notes": "[offline] dữ liệu giả, không dùng để phân tích"}
