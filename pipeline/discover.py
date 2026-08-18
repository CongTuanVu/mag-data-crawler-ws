"""Bước 1 — Agent tự tìm nguồn trên mạng.

Input: một dòng tên toà nhà ("Marina One Residences, Singapore").
Output: định danh toà nhà (registry) + bảng nguồn URL đã tuyển, ghi ra
output_raw/<building_id>/sources.json.

Agent dùng server tool web_search + web_fetch của Claude API — Anthropic chạy
tìm kiếm phía server, không cần API search riêng.

Đây là bước TỐN TOKEN NHẤT của pipeline (nội dung trang fetch về nằm lại trong
context suốt tool loop), nên ngân sách search/fetch, trần nội dung mỗi trang, số
nguồn nhắm tới và effort đều đặt được ở pipeline/config.py.
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
0. TRƯỚC KHI SEARCH, lập kế hoạch từ khoá từ feature_spec đính kèm — đó là nguồn
   sự thật, không dùng trí nhớ của bạn:
   · §11 cho biết mỗi nhóm feature nên tìm ở LOẠI trang nào → dịch thành truy vấn
     riêng cho từng purpose, đừng gộp một truy vấn chung cho cả toà.
   · §10 liệt kê thuật ngữ bản địa mà nguồn thật dùng để công bố số liệu
     (`物件概要`, `間取り`, `専有面積`, `壁芯面積`, `坪`, `帖`/`畳`, `万円`, `億円`,
     `공급면적`, `전용면적`, `건축면적`, `建筑面积`, `套内面积`, `户型图`,
     `tỷ đồng`, `triệu/m²`). Ghép TÊN BẢN ĐỊA của toà với các thuật ngữ này —
     trang chứa đúng những chữ đó mới là trang có số liệu trích được.
   · §9 là danh mục `purpose` hợp lệ; mỗi nguồn phải gán đúng một giá trị.
   Ví dụ với một toà ở Nhật: `<tên> 物件概要`, `<tên> 間取り`, `<tên> 専有面積`,
   `<tên> 設備 仕様`, `<tên> 価格 万円`, `<tên> 共用施設` — mỗi truy vấn nhắm một purpose.
1. Tìm bằng CẢ tiếng Anh VÀ ngôn ngữ bản địa. Nguồn bản địa gần như luôn giàu số
   liệu hơn nguồn tiếng Anh; tìm bản địa trước.
2. Ưu tiên theo đúng cột "Nguồn ưu tiên" của §11, và nói chung: trang chính thức
   CĐT > trang công ty kiến trúc / ArchDaily / CTBUH > portal niêm yết lớn >
   báo ngành. Brochure PDF là nguồn quý nhất cho mặt bằng, bàn giao và product
   mix — luôn cố tìm.
2b. §11 cảnh báo nhiều portal niêm yết chặn crawl: kiểm tra `robots.txt` trước
   khi đưa vào, bị chặn thì thay bằng báo cáo thị trường. KHÔNG vượt rào.
3. web_fetch để KIỂM CHỨNG, và chỉ khi cần. Ngân sách mỗi toà: {search_uses} lượt
   search + {fetch_uses} lượt fetch, mỗi fetch chỉ trả về ~{fetch_tokens} token ĐẦU
   trang (bị cắt có chủ ý). Vậy nên:
   · Kết quả tìm kiếm đã đủ chắc (đúng tên toà + đúng loại trang) → đưa vào luôn,
     KHÔNG fetch. Dành lượt fetch cho trang bạn thực sự nghi ngờ.
   · Fetch là để trả lời "trang này có phải của ĐÚNG toà này và có bảng số liệu
     không", KHÔNG phải để đọc số. Việc bóc số là của bước sau.
   · Đừng fetch cùng một domain nhiều lần để tìm trang con — suy ra đường dẫn từ
     trang chủ CĐT (/outline/, /plan/, /quality/) rồi đưa thẳng vào danh sách.
4. Cần phủ đủ các purpose: official_overview, floorplan, brochure_pdf, amenities,
   handover_spec, product_mix, price_primary, price_secondary, architecture.
   Nhắm {src_min}–{src_max} nguồn, ưu tiên trang giàu số liệu hơn là gom cho đủ số.
   Thiếu nhóm nào thì ghi vào `gaps`, KHÔNG bịa URL.
5. Nếu tên toà mơ hồ hoặc trùng nhiều dự án: chọn ứng viên khớp nhất và giải
   thích trong `disambiguation_note`. Nếu không xác minh được toà có thật →
   `found = false`, `sources` để rỗng.

Chỉ trả URL bạn đã thấy tồn tại, hoặc suy ra được từ cấu trúc điều hướng của
chính trang chủ CĐT mà bạn đã xem. Không đoán đường dẫn theo mẫu chung chung.

`expected_content` viết NGẮN — một dòng nêu trang đó cho trường nào, không chép
lại số liệu bạn thấy (bước sau tự đọc từ raw).
"""


def system_prompt() -> str:
    return SYSTEM.format(search_uses=config.WEB_SEARCH_USES,
                         fetch_uses=config.WEB_FETCH_USES,
                         fetch_tokens=f"{config.WEB_FETCH_TOKENS:,}",
                         src_min=config.SOURCES_MIN, src_max=config.SOURCES_MAX)


def run(query: str, out_dir: Path) -> Dict[str, Any]:
    print(f"[1/4] Tìm nguồn cho: {query}")
    result = llm.call_json(
        # Spec là nguồn sự thật cho từ khoá tìm kiếm (§10, §11) và danh mục
        # purpose (§9) — gửi nguyên văn thay vì chép lại vào prompt, để hai nơi
        # không lệch nhau khi spec đổi.
        system=[{"type": "text", "text": system_prompt()},
                llm.cached("<feature_spec>\n"
                           + config.read_spec_sections(config.SPEC_SECTIONS_DISCOVER)
                           + "\n</feature_spec>")],
        user_content=f"Toà nhà cần khảo sát: {query}\n\nHôm nay: {date.today().isoformat()}",
        schema=SCHEMA,
        tools=llm.WEB_TOOLS,
        max_tokens=24000,
        effort=config.EFFORT_DISCOVER,
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
