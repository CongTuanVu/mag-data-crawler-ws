"""Đường dẫn dữ liệu — mọi thứ đọc từ biến môi trường để compose gắn ở đâu cũng chạy.

Không có giá trị nào trỏ vào máy cụ thể: mặc định là đường trong container, còn
compose ánh xạ chúng sang đường thật trên host, chỉ đọc.
"""
from __future__ import annotations

import os
from pathlib import Path


def _p(env: str, default: str) -> str:
    return os.environ.get(env, default).rstrip("/")


CORPUS_DIR = _p("CORPUS_DIR", "/data/corpus")
JP_CSV_DIR = _p("JP_CSV_DIR", "/data/output_csv")
# Lưu ý: nguồn tài liệu ĐÃ CHUYỂN CHỖ trên host — `/srv/ws1/data/vinhhd/` không
# còn, nay ở `/mnt/data/ws1-data/vinhhd/`. Compose trỏ đúng chỗ mới.
DOCS_INDEX = os.environ.get("DOCS_INDEX", "/data/mdindex/index.db")

MEMORY_LIMIT = os.environ.get("DUCKDB_MEMORY_LIMIT", "4GB")
MAX_LIMIT = int(os.environ.get("MAX_PAGE_SIZE", "200"))

# Nhiều mã thị trường cùng một nước, khác nguồn — gộp lại như trang đang làm.
MARKET_GROUPS = {
    "taiwan": ["taiwan", "taiwan_ext", "taiwan_new"],
    "poland": ["poland", "poland_korter"],
}

MARKET_VI = {
    "korea": "Hàn Quốc", "singapore": "Singapore", "taiwan": "Đài Loan",
    "switzerland": "Thuỵ Sĩ", "netherlands": "Hà Lan", "usa": "Hoa Kỳ",
    "denmark": "Đan Mạch", "estonia": "Estonia", "latvia": "Latvia",
    "france": "Pháp", "uruguay": "Uruguay", "malaysia": "Malaysia",
    "russia": "Nga", "poland": "Ba Lan", "kazakhstan": "Kazakhstan",
    "georgia": "Gruzia", "hongkong": "Hong Kong", "uk": "Anh",
    "azerbaijan": "Azerbaijan", "moldova": "Moldova",
}


def corpus(name: str) -> str:
    """Tên bảng → đường dẫn parquet, dùng trong SQL."""
    return f"{CORPUS_DIR}/{name}.parquet"


def exists(name: str) -> bool:
    return Path(corpus(name)).exists()
