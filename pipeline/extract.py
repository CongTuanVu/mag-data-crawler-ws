"""Bước 3a — Trích feature từ text raw.

Toàn bộ corpus raw của một toà nhà nạp một lần vào system prompt có cache
breakpoint, rồi gọi 6 lượt (mỗi bảng text một lượt, B3 do vision lo). Từ lượt
thứ hai trở đi corpus đọc từ cache nên chi phí ~0.1x.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from . import config, llm, schema

MAX_CHARS_PER_FILE = 80_000
MAX_CHARS_TOTAL = 900_000

SYSTEM = """\
Bạn là extractor cho workstream WS1 Building. Nguồn sự thật duy nhất là
feature_spec đính kèm và corpus raw đính kèm. Quy tắc bắt buộc:

1. KHÔNG BỊA. Nguồn không nêu → null. Cấm nội suy diện tích phòng từ tổng diện
   tích căn, cấm chia đều num_units_total / num_floors, cấm suy số từ ảnh phối cảnh.
2. Chỉ trích từ corpus đính kèm. Không dùng kiến thức nền của bạn về toà nhà này.
   Nếu bạn "biết" một con số nhưng corpus không có → null.
3. Quy đổi đơn vị NGAY KHI TRÍCH theo feature_spec §10 (평/坪 → ×3.30579,
   帖/畳 → ×1.62, sq ft → ×0.092903, 억원 → ×1e8, 億円 → ×1e8, 万元 → ×1e4,
   万円 → ×1e4, tỷ đồng → ×1e9, triệu/m² → ×1e6).
   `snippet` giữ NGUYÊN VĂN số gốc chưa quy đổi.
4. Cơ sở diện tích/giá là bắt buộc: area_basis_reported / price_basis. Hàn Quốc
   공급면적 → tim_tuong, 전용면적 → thong_thuy; Trung Quốc 建筑面积 → tim_tuong,
   套内面积 → thong_thuy; Nhật Bản 壁芯面積 → tim_tuong, 専有面積/内法面積 →
   thong_thuy. KHÔNG tự quy đổi giữa hai cơ sở.
4b. Nhật Bản: 間取り `nLDK` → layout_class `npn` (LDK KHÔNG tính là phòng ngủ):
   1LDK→1pn, 2LDK→2pn, 3LDK→3pn, 4LDK→4pn_plus, 1R/1K→studio. Hậu tố `+S`
   (サービスルーム) → biến thể `_plus` và has_multipurpose_room = true.
   Giá Nhật thường công bố theo CĂN → price_unit = per_unit; không tự chia ra
   đơn giá/m² nếu nguồn không nêu.
5. Mỗi trường khác null PHẢI có đúng một dòng provenance với `source_file` là tên
   file trong corpus (vd 03_brochure.txt) và `snippet` là câu/ô gốc chứa giá trị.
   Trường không có provenance sẽ bị loại ở bước kiểm tra.
6. confidence: high = trang chính thức CĐT hoặc báo cáo có tên đơn vị + kỳ;
   medium = bên thứ ba uy tín; low = giá trị suy từ tin rao lẻ hoặc bị che khuất.
7. Nguồn mâu thuẫn → lấy nguồn chính thức/mới hơn, ghi mâu thuẫn vào `notes`.
8. NGÔN NGỮ ĐẦU RA — corpus có thể là tiếng Nhật/Hàn/Trung/Anh, CSV thì luôn
   chuẩn hoá. Ba nhóm, xử lý khác nhau:
   8a. Mọi trường mô tả (item_name, item_spec, amenity_name, orientation_note,
       design_concept, design_concept_keywords, signature_features, floor_level,
       area_basis_note, notes, và mọi trường văn xuôi khác) → viết bằng TIẾNG VIỆT.
       Dịch nghĩa, không phiên âm, không kèm bản gốc trong ngoặc — bản gốc đã nằm
       ở `snippet`. Vd 生ゴミディスポーザー → `Máy nghiền rác thực phẩm`;
       スライド式食器洗い乾燥機 → `Máy rửa & sấy bát kiểu trượt`.
   8b. Danh từ riêng (developer, architect_firm, brand, operator_brand, district,
       city, country, project_name, building_name, address) → viết bằng CHỮ
       LA-TINH: dùng tên tiếng Anh chính thức nếu doanh nghiệp có, nếu không thì
       romaji/phiên âm La-tinh. KHÔNG dịch nghĩa tên riêng, KHÔNG để chữ Nhật/Hàn/
       Trung. Vd 住友不動産株式会社 → `Sumitomo Realty & Development`;
       中央区 → `Quận Chuo`; 鳴海製陶 → `Narumi`. Phần chức danh chung trong địa
       chỉ thì dịch (丁目 → `chome`, 区 → `Quận`).
   8c. GIỮ NGUYÊN VĂN, tuyệt đối không dịch: mọi trường `*_local`
       (building_name_local, amenity_name_local), `snippet` trong provenance, và
       `type_code` — type_code phải khớp ký tự với nguồn để B3/B7 nối được.
   Mã danh mục (layout_class, handover_standard, market, area_basis_reported…)
   dùng đúng giá trị enum trong feature_spec §9, không dịch lại.
"""

TABLE_HINTS = {
    "building": "Trả về ĐÚNG 1 record cho toà nhà này. Các trường tóm tắt "
                "(§1.3, §1.4) và efficiency_ratio_pct do code tự tính — không có trong schema.",
    "unit_type": "Mỗi loại căn CĐT công bố là 1 record. type_code phải khớp đúng mã trong "
                 "nguồn để B3/B7 nối được. Chỉ điền area_gross_m2 và area_net_m2 khi nguồn "
                 "nêu rõ từng cơ sở; thiếu một trong hai → để null, KHÔNG quy đổi.",
    "floor_plate": "Một toà nhiều tháp / nhiều dải tầng → nhiều record. Đừng ép 1 con số "
                   "duy nhất. Không có mặt bằng tầng dạng text → trả records rỗng.",
    "handover_item": "Chỉ is_included = true khi hạng mục nằm dưới tiêu đề bàn giao/handover/"
                     "specification/사양/交付标准. Mục có option, nâng cấp, upgrade, phụ thu, "
                     "オプション → is_included = false.",
    "amenity": "CHỈ tiện ích nội khu của toà. Trường học, bệnh viện, TTTM ngoài ranh giới toà "
               "là tiện ích cấp khu đô thị (thuộc WS1 khác) — KHÔNG đưa vào đây.",
    "price_obs": "Tách rõ so_cap (CĐT mở bán) và thu_cap (chuyển nhượng); không gộp. Giá rao "
                 "≠ giá giao dịch → phân biệt bằng source_type. Không nêu VAT → includes_vat "
                 "= null, KHÔNG mặc định false. Mỗi kỳ quan sát là một record riêng.",
}


def build_corpus(out_dir: Path, manifest: Dict[str, Any]) -> str:
    """Ghép các file .txt đã crawl thành một corpus có nhãn nguồn."""
    parts, total = [], 0
    for s in manifest["sources"]:
        tf = s.get("text_file")
        if not tf or s.get("status") != "ok":
            continue
        path = out_dir / tf
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if len(text) > MAX_CHARS_PER_FILE:
            text = text[:MAX_CHARS_PER_FILE] + "\n…[cắt bớt]"
        head = (f"=== FILE: {Path(tf).name} | URL: {s['url']} | purpose: {s.get('purpose','')} "
                f"| accessed_at: {s.get('accessed_at','')} ===")
        block = head + "\n" + text
        if total + len(block) > MAX_CHARS_TOTAL:
            print(f"      ! corpus chạm trần {MAX_CHARS_TOTAL:,} ký tự, bỏ qua từ {Path(tf).name}")
            break
        parts.append(block)
        total += len(block)
    print(f"      corpus: {len(parts)} file, {total:,} ký tự")
    return "\n\n".join(parts)


def run(out_dir: Path, manifest: Dict[str, Any], resolved: Dict[str, Any]) -> Dict[str, Any]:
    print("[3/4] Trích feature từ text")
    corpus = build_corpus(out_dir, manifest)
    if not corpus.strip():
        raise SystemExit("Corpus rỗng — không có trang nào crawl thành công.")

    system = [
        {"type": "text", "text": SYSTEM},
        {"type": "text", "text": "<feature_spec>\n" + config.read_spec() + "\n</feature_spec>"},
        llm.cached("<corpus_raw>\n" + corpus + "\n</corpus_raw>"),
    ]
    ident = (f"Toà nhà: {resolved['building_name']}"
             f"{' / ' + resolved['building_name_local'] if resolved.get('building_name_local') else ''}"
             f" — {resolved['city']}, {resolved['country']}")

    out: Dict[str, Any] = {}
    for name in schema.TEXT_TABLES:
        t = schema.TABLES[name]
        user = (f"{ident}\n\n"
                f"Trích bảng {t.label} `{t.name}` — {t.unit}.\n"
                f"Đọc mục tương ứng trong feature_spec để hiểu từng trường.\n"
                f"{TABLE_HINTS[name]}\n\n"
                f"Duyệt TOÀN BỘ corpus trước khi trả lời. Không có dữ liệu → records rỗng.")
        res = llm.call_json(system=system, user_content=user, schema=schema.json_schema(t),
                            label=f"extract:{name}")
        out[name] = res
        print(f"      {t.label} {name}: {len(res['records'])} record"
              + (f" · {res['notes'][:90]}" if res.get("notes") else ""))
    (out_dir / "extract_text.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
