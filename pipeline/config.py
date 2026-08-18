"""Cấu hình chung: đường dẫn, model, khoá API, tỷ giá.

Thứ tự tìm ANTHROPIC_API_KEY:
  1. biến môi trường
  2. <repo>/.env
  3. ../mag-research-agent/.env   (tiện dụng — đã có sẵn key ở máy này)
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "output_raw"
CSV_DIR = ROOT / "output_csv"

# Spec có thể nằm ở features/ (vị trí chuẩn, giống repo mag-data-crawler) hoặc ở
# thư mục gốc. Chọn file ĐẦU TIÊN thực sự là spec WS1 Building — có mặt bảng B3 —
# để không vớ nhầm file cùng tên của prototype khác.
_SPEC_CANDIDATES = [ROOT / "features" / "ws1_building" / "feature_spec.md",
                    ROOT / "ws1_building" / "feature_spec.md",
                    ROOT / "features" / "ws2_building" / "feature_spec.md"]


def _pick_spec() -> Path:
    found = [p for p in _SPEC_CANDIDATES if p.exists()]
    for p in found:
        if "unit_room" in p.read_text(encoding="utf-8", errors="ignore"):
            return p
    return found[0] if found else _SPEC_CANDIDATES[0]


SPEC_PATH = _pick_spec()

# ── Model ────────────────────────────────────────────────────────────────────
OFFLINE = os.environ.get("WS1_OFFLINE", "") == "1"   # bật bằng --offline: giả lập model

# Trỏ sang endpoint khác (proxy nội bộ, LiteLLM gateway, model chạy local…).
# Endpoint đó phải nói được Messages API. SDK tự đọc biến này, khai lại ở đây để
# quyết định có bật chế độ tương thích hay không.
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "").strip()

# Chế độ tương thích: KHÔNG dùng 3 tính năng chỉ có ở API Anthropic chính thức
#   output_config.format (structured output) → hướng dẫn JSON trong prompt + tự parse
#   cache_control (prompt caching)           → bỏ, chấp nhận tốn token
#   web_search/web_fetch (server tool)       → không có, bước discover sẽ báo lỗi rõ
COMPAT = os.environ.get("WS1_COMPAT", "1" if BASE_URL else "0") == "1"

MODEL = os.environ.get("WS1_MODEL", "claude-opus-5")
EFFORT = os.environ.get("WS1_EFFORT", "high")        # low|medium|high|xhigh|max
MAX_TOKENS = int(os.environ.get("WS1_MAX_TOKENS", "32000"))

# ── Van tiết kiệm token cho bước [1] tìm nguồn ───────────────────────────────
# Đây là bước duy nhất còn gọi model khi chạy đường nhanh, và là bước đắt nhất:
# mỗi `web_fetch` nạp trọn nội dung một trang vào context, rồi context đó được
# mang theo qua từng vòng của server tool — chi phí tăng theo BÌNH PHƯƠNG số
# trang đọc. Ba con số dưới đây quyết định phần lớn hoá đơn.
WEB_SEARCH_USES = int(os.environ.get("WS1_WEB_SEARCH_USES", "6"))
WEB_FETCH_USES = int(os.environ.get("WS1_WEB_FETCH_USES", "5"))
# Trần nội dung MỖI trang web_fetch kéo về. Không đặt = trọn trang (có trang
# 40.000+ token). 4.000 đủ để xác minh trang có bảng 物件概要 hay không —
# việc bóc số liệu là của bước [3], không phải bước này.
WEB_FETCH_TOKENS = int(os.environ.get("WS1_WEB_FETCH_TOKENS", "4000"))

# Số nguồn nhắm tới. Ít nguồn = ít fetch ở bước [1], ít trang crawl ở bước [2],
# corpus nhỏ hơn ở bước [3]. Cắt ở đây tiết kiệm cả ba bước.
SOURCES_MIN = int(os.environ.get("WS1_SOURCES_MIN", "10"))
SOURCES_MAX = int(os.environ.get("WS1_SOURCES_MAX", "16"))

# Bước tìm nguồn không cần suy luận sâu như bước trích số liệu — nó chỉ tuyển
# URL. Hạ effort riêng cho bước này cắt được phần lớn token suy nghĩ.
EFFORT_DISCOVER = os.environ.get("WS1_EFFORT_DISCOVER", "medium")

# Gửi cả feature_spec (30k ký tự) cho bước tìm nguồn là thừa: nó chỉ dùng §9
# (danh mục purpose), §10 (thuật ngữ bản địa) và §11 (nguồn ưu tiên) — 17% spec.
SPEC_SECTIONS_DISCOVER = os.environ.get("WS1_SPEC_SECTIONS_DISCOVER", "9,10,11")

# ── Tỷ giá quy đổi sang USD (chỉ dùng cho cột derived price_usd_per_m2) ──────
# Cập nhật tay khi cần; ghi rõ ngày để mọi con số USD truy được nguồn tỷ giá.
FX_DATE = "2026-08-01"
FX_TO_USD = {
    "USD": 1.0,
    "VND": 1 / 26_100,
    "KRW": 1 / 1_390,
    "CNY": 1 / 7.15,
    "JPY": 1 / 155.0,
    "SGD": 1 / 1.30,
    "EUR": 1.08,
    "TWD": 1 / 32.5,
    "HKD": 1 / 7.8,
    "THB": 1 / 34.5,
}


def load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    if BASE_URL:                      # proxy/model local thường không cần key thật
        return os.environ.get("ANTHROPIC_AUTH_TOKEN", "local")
    for env_path in (ROOT / ".env", ROOT.parent / "mag-research-agent" / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*(.+)\s*$", line)
            if m:
                val = m.group(1).strip().strip('"').strip("'")
                if val and not val.startswith("sk-ant-xxx"):
                    os.environ["ANTHROPIC_API_KEY"] = val
                    return val
    raise SystemExit(
        "Không tìm thấy ANTHROPIC_API_KEY.\n"
        "  export ANTHROPIC_API_KEY=sk-ant-...   hoặc tạo file .env trong thư mục dự án."
    )


def slugify(text: str, fallback: str = "x") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s[:60] or fallback


def read_spec() -> str:
    text = SPEC_PATH.read_text(encoding="utf-8")
    if "unit_room" not in text:      # chặn nhầm với file stub cùng tên
        raise SystemExit(f"{SPEC_PATH} không phải feature_spec WS1 Building (thiếu bảng B3).")
    return text


def read_spec_sections(sections: str = "") -> str:
    """Chỉ vài mục cấp 2 của spec, vd `9,10,11`. Rỗng/`all` → trọn spec.

    Giữ nguyên tiêu đề file ở đầu để model biết đang đọc spec nào, và ghi rõ
    phần nào bị lược để nó không tưởng spec chỉ có ngần ấy.
    """
    text = read_spec()
    wanted = [s.strip() for s in (sections or "").split(",") if s.strip()]
    if not wanted or "all" in wanted:
        return text
    blocks = re.split(r"(?m)^(?=## )", text)
    head = blocks[0] if blocks and not blocks[0].startswith("## ") else ""
    keep, dropped = [], 0
    for block in blocks:
        m = re.match(r"## (\d+)\.", block.strip())
        if m and m.group(1) in wanted:
            keep.append(block)
        elif block is not head:
            dropped += 1
    if not keep:                     # số mục sai → thà gửi thừa còn hơn thiếu
        return text
    note = (f"\n\n[Đã lược {dropped} mục khác của spec — bước này chỉ cần mục "
            f"{', '.join('§' + w for w in wanted)}.]\n")
    return head + note + "\n".join(keep)
