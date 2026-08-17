"""Bước 1 — Agent tự tìm nguồn trên mạng.

Input: một dòng tên toà nhà ("Marina One Residences, Singapore").
Output: định danh toà nhà (registry) + bảng nguồn URL đã tuyển, ghi ra
output_raw/<building_id>/sources.json.

Agent dùng server tool web_search + web_fetch của Claude API — Anthropic chạy
tìm kiếm phía server, không cần API search riêng.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict

from . import config, llm

PURPOSES = ["official_overview", "floorplan", "brochure_pdf", "amenities", "handover_spec",
            "product_mix", "price_primary", "price_secondary", "architecture",
            "news_report", "market_report"]

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "resolved": {
            "type": "object",
            "properties": {
                "found": {"type": "boolean"},
                "building_name": {"type": "string", "description": "Tên chuẩn (tiếng Anh/quốc tế)"},
                "building_name_local": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "project_name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "country": {"type": "string"},
                "city": {"type": "string"},
                "official_website": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "developer": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "building_id_suggestion": {"type": "string",
                                           "description": "slug snake_case ascii, vd songdo_central_park_ipark"},
                "disambiguation_note": {"type": "string",
                                        "description": "Vì sao chắc đây đúng toà; nêu rõ nếu có toà trùng tên"},
                "search_languages": {"type": "array", "items": {"type": "string"},
                                     "description": "Ngôn ngữ đã dùng để tìm, vd ko, zh, vi, en"},
            },
            "required": ["found", "building_name", "building_name_local", "project_name", "country",
                         "city", "official_website", "developer", "building_id_suggestion",
                         "disambiguation_note", "search_languages"],
            "additionalProperties": False,
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "purpose": {"type": "string", "enum": PURPOSES},
                    "expected_content": {"type": "string",
                                         "description": "Trang này dùng để lấy trường nào của spec"},
                    "is_official": {"type": "boolean", "description": "Trang của CĐT/đơn vị thiết kế"},
                    "language": {"type": "string"},
                    "priority": {"type": "integer", "description": "1 = phải crawl, 3 = có thì tốt"},
                },
                "required": ["url", "title", "purpose", "expected_content", "is_official",
                             "language", "priority"],
                "additionalProperties": False,
            },
        },
        "gaps": {"type": "string", "description": "Nhóm feature nào chưa tìm được nguồn"},
    },
    "required": ["resolved", "sources", "gaps"],
    "additionalProperties": False,
}

SYSTEM = """\
Bạn là chuyên viên khảo sát nguồn dữ liệu bất động sản quốc tế cho một dự án
benchmark toà nhà ở & mixed-use. Nhiệm vụ: từ tên một toà nhà, xác minh toà đó
có thật rồi tuyển một bảng nguồn URL đủ để trích 7 nhóm feature.

Cách làm:
1. Tìm bằng CẢ tiếng Anh VÀ ngôn ngữ bản địa của thị trường đó (Hàn: 아파트/분양,
   Trung: 户型图/建筑面积, Nhật: 間取り/物件概要, Việt: mặt bằng/tiêu chuẩn bàn giao).
   Nguồn bản địa gần như luôn giàu số liệu hơn nguồn tiếng Anh.
2. Ưu tiên: trang chính thức CĐT > trang công ty kiến trúc / ArchDaily / CTBUH >
   portal niêm yết lớn > báo ngành. Brochure PDF là nguồn quý nhất cho mặt bằng,
   bàn giao và product mix — luôn cố tìm.
3. web_fetch vài trang ứng viên để KIỂM CHỨNG trang thật sự chứa số liệu trước
   khi đưa vào danh sách. Không liệt kê link chỉ dựa vào tiêu đề kết quả tìm kiếm.
4. Cần phủ đủ các purpose: official_overview, floorplan, brochure_pdf, amenities,
   handover_spec, product_mix, price_primary, price_secondary, architecture.
   Nhắm 12–25 nguồn. Thiếu nhóm nào thì ghi vào `gaps`, KHÔNG bịa URL.
5. Nếu tên toà mơ hồ hoặc trùng nhiều dự án: chọn ứng viên khớp nhất và giải
   thích trong `disambiguation_note`. Nếu không xác minh được toà có thật →
   `found = false`, `sources` để rỗng.

Chỉ trả URL bạn đã thấy tồn tại. Không đoán đường dẫn theo mẫu.
"""


def run(query: str, out_dir: Path) -> Dict[str, Any]:
    print(f"[1/4] Tìm nguồn cho: {query}")
    result = llm.call_json(
        system=[{"type": "text", "text": SYSTEM}],
        user_content=f"Toà nhà cần khảo sát: {query}\n\nHôm nay: {date.today().isoformat()}",
        schema=SCHEMA,
        tools=llm.WEB_TOOLS,
        max_tokens=24000,
        label="discover",
    )
    r = result["resolved"]
    if not r["found"]:
        raise SystemExit(f"Không xác minh được toà nhà '{query}'. {r['disambiguation_note']}")

    result["query"] = query
    result["discovered_at"] = date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sources.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    srcs = sorted(result["sources"], key=lambda s: s["priority"])
    print(f"      → {r['building_name']} ({r['city']}, {r['country']}) · {len(srcs)} nguồn")
    for s in srcs:
        print(f"        [{s['priority']}] {s['purpose']:<18} {s['url'][:88]}")
    if result["gaps"]:
        print(f"      ! thiếu nguồn: {result['gaps']}")
    return result


def building_id(resolved: Dict[str, Any], override: str = "") -> str:
    return config.slugify(override or resolved["building_id_suggestion"], "building")
