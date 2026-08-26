"""Kết nối DuckDB và SQLite, mở một lần lúc khởi động.

DuckDB đọc thẳng parquet, KHÔNG nạp trước và KHÔNG có bước ETL: parquet đã là
định dạng cột nên lọc/gộp chạy tại chỗ. Đo trên máy này (8 nhân, parquet 133 MB):

    lọc 1 thị trường + sắp xếp, lấy 50 toà       23,5 ms
    phân vị 4 chỉ tiêu trên toàn 618k dòng       29,5 ms
    quét ILIKE tên toà toàn kho                 158,2 ms   ← chậm nhất

Một `Connection` dùng chung cho cả tiến trình, mỗi request lấy một `cursor()`.
Đây là cách DuckDB khuyến nghị: bản thân connection không an toàn khi nhiều luồng
cùng gọi, còn cursor thì tách phiên riêng. FastAPI chạy endpoint đồng bộ trong
threadpool nên bắt buộc phải theo cách này.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any, Iterable

import duckdb

from . import config

_con: duckdb.DuckDBPyConnection | None = None
_sqlite_lock = threading.Lock()
_sqlite: sqlite3.Connection | None = None


def connect() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        _con = duckdb.connect(database=":memory:")
        _con.execute(f"PRAGMA memory_limit='{config.MEMORY_LIMIT}'")
        # /tmp trong container là tmpfs ở một số nơi; ném file tràn ra đó sẽ ăn RAM
        _con.execute("PRAGMA temp_directory='/tmp/duckdb-spill'")
    return _con


def q(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    """Chạy truy vấn, trả list dict. Tham số LUÔN bind, không nội suy chuỗi."""
    cur = connect().cursor()
    try:
        cur.execute(sql, list(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Iterable[Any] = ()):
    r = one(sql, params)
    return next(iter(r.values())) if r else None


def docs_db() -> sqlite3.Connection | None:
    """Chỉ số toàn văn của bộ tài liệu — SQLite FTS5, 817 MB, mở chỉ đọc.

    Không dùng chung với DuckDB: đây là file SQLite thật, và nó do một pipeline
    khác ghi (`mdindex/ingest.sh`) nên phải mở read-only để không giữ khoá.
    """
    global _sqlite
    if _sqlite is None:
        import os
        if not os.path.exists(config.DOCS_INDEX):
            return None
        _sqlite = sqlite3.connect(
            f"file:{config.DOCS_INDEX}?mode=ro", uri=True, check_same_thread=False)
        _sqlite.row_factory = sqlite3.Row
    return _sqlite


def docs_query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    db = docs_db()
    if db is None:
        return []
    with _sqlite_lock:                     # sqlite3 một connection, phải khoá
        return [dict(r) for r in db.execute(sql, list(params)).fetchall()]
