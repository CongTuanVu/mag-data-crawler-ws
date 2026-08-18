"""Dịch gộp các thuật ngữ mà từ điển chưa có (feature_spec quy tắc 8a/8b).

Code bóc tách không dịch được, nên nó giữ nguyên văn và ghi lại term trượt vào
`code_extract/.lexicon_misses.json`. Bước này gom TẤT CẢ term đó của cả mẻ 209
toà, dịch theo lô lớn (mặc định 250 term/lượt) rồi ghi vào `lexicon_auto.json`.
Lần chạy sau tra được ngay — chi phí LLM tiến dần về 0.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

from code_extract import lexicon

from . import config, llm

BATCH = int(os.environ.get("WS1_TRANSLATE_BATCH", "250"))

SYSTEM = """\
Bạn dịch thuật ngữ bất động sản Nhật/Hàn/Trung sang tiếng Việt cho một bộ dữ liệu
đã chuẩn hoá. Với MỖI term đầu vào, trả đúng một bản dịch theo hai quy tắc:

8a. Từ mô tả (thiết bị, vật liệu, tiện ích, kết cấu, hướng, ghi chú) → TIẾNG VIỆT.
    Dịch nghĩa, không phiên âm, KHÔNG kèm bản gốc trong ngoặc.
    生ゴミディスポーザー → Máy nghiền rác thực phẩm
    食器洗い乾燥機全住戸標準採用 → Máy rửa & sấy bát, trang bị tiêu chuẩn cho toàn bộ căn hộ

8b. Danh từ riêng (chủ đầu tư, đơn vị thiết kế, thương hiệu, quận/phường, tên dự án,
    địa chỉ) → CHỮ LA-TINH: tên tiếng Anh chính thức nếu doanh nghiệp có, không thì
    romaji. KHÔNG dịch nghĩa tên riêng.
    住友不動産株式会社 → Sumitomo Realty & Development
    中央区 → Quận Chuo          (phần chức danh chung thì dịch: 丁目 → chome)
    鳴海製陶 → Narumi

Giữ nguyên con số, đơn vị, mã hiệu, tên thương hiệu La-tinh. Không thêm giải thích.
Term không hiểu nghĩa → để `vi` là chuỗi rỗng, đừng đoán bừa.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "src": {"type": "string", "description": "Term gốc, chép lại y nguyên"},
                    "vi": {"type": "string", "description": "Bản dịch; rỗng nếu không chắc"},
                },
                "required": ["src", "vi"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["terms"],
    "additionalProperties": False,
}


def pending() -> Dict[str, int]:
    """Term chưa dịch, đã trừ những gì lexicon đã có."""
    try:
        misses = json.loads(lexicon.MISS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    known = set(lexicon.SEED) | set(lexicon._load_auto())
    return {k: v for k, v in misses.items()
            if k not in known and isinstance(v, int) and k.strip()}


def run(*, limit: int = 0, dry_run: bool = False) -> int:
    """Dịch hết term đang chờ. Trả số mục mới thêm vào từ điển."""
    todo = pending()
    if not todo:
        print("✓ Không còn thuật ngữ nào chờ dịch.")
        return 0
    # Term xuất hiện nhiều lần được dịch trước — có cắt bớt thì cắt phần đuôi hiếm.
    terms = [t for t, _ in sorted(todo.items(), key=lambda kv: (-kv[1], kv[0]))]
    if limit:
        terms = terms[:limit]
    batches = [terms[i:i + BATCH] for i in range(0, len(terms), BATCH)]
    print(f"{len(terms)} thuật ngữ chờ dịch → {len(batches)} lượt gọi model "
          f"({BATCH} term/lượt)")
    if dry_run:
        for t in terms[:40]:
            print(f"  {todo[t]:>4}× {t}")
        if len(terms) > 40:
            print(f"  … còn {len(terms) - 40} term")
        return 0

    config.load_api_key()
    added = 0
    for i, batch in enumerate(batches, 1):
        user = ("Dịch các thuật ngữ sau. Trả đủ, đúng thứ tự, `src` chép y nguyên:\n\n"
                + "\n".join(f"{n}. {t}" for n, t in enumerate(batch, 1)))
        res = llm.call_json(system=[{"type": "text", "text": SYSTEM}], user_content=user,
                            schema=SCHEMA, label=f"translate:{i}/{len(batches)}")
        pairs = {r["src"]: r["vi"] for r in res.get("terms", [])
                 if r.get("src") and r.get("vi")}
        n = lexicon.merge_auto(pairs)
        added += n
        print(f"      lượt {i}/{len(batches)}: +{n} mục (tổng {added})")
    print(f"✓ Từ điển bổ sung {added} mục → {lexicon.AUTO_PATH}")
    return added
