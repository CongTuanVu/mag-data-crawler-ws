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
