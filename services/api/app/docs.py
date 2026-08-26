"""Tìm toàn văn 22.559 tài liệu — SQLite FTS5, 817 MB, mở chỉ đọc.

Hai bảng ảo, dùng cho hai họ chữ viết khác nhau:

    fts       unicode61 remove_diacritics 2   — chữ Latin, Việt, Nga…
    fts_cjk   trigram                          — Trung, Nhật, Hàn

Truy vấn Latin mà bắn vào bảng trigram thì kết quả rác, nên phải chọn bảng theo
chữ trong từ khoá. Đo tay: cả hai bảng đều dưới 10 ms kể cả khi join đủ.

`q` là chữ NGUYÊN VĂN người dùng gõ. FTS5 coi một số ký tự là cú pháp (`"` `*`
`:` `-` `NEAR` `AND` `OR`), gõ nhầm là lỗi cú pháp chứ không phải không có kết
quả — nên bọc từ khoá thành chuỗi trích dẫn, tìm đúng cụm chữ người ta gõ.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from functools import lru_cache

from . import config

_lock = threading.Lock()
_db: sqlite3.Connection | None = None
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def db() -> sqlite3.Connection | None:
    global _db
    if _db is None:
        import os
        if not os.path.exists(config.DOCS_INDEX):
            return None
        _db = sqlite3.connect(f"file:{config.DOCS_INDEX}?mode=ro", uri=True,
                              check_same_thread=False)
        _db.row_factory = sqlite3.Row
    return _db


def _phrase(q: str) -> str:
    """Bọc thành chuỗi trích dẫn để ký tự cú pháp của FTS5 không làm nổ truy vấn."""
    return '"' + q.replace('"', '""') + '"'


ROOT_VI = {"mag": "MAG — nghiên cứu thị trường", "kđt": "KĐT — khu đô thị"}


def _top(rows: list, k: int, key="k") -> list[dict]:
    """top k rồi gom đuôi — cùng cách trang gộp đuôi tỉnh."""
    head = [dict(r) for r in rows[:k]]
    rest = rows[k:]
    if rest:
        head.append({key: "Còn lại", "n": sum(r["n"] for r in rest), "rest": len(rest)})
    return head


@lru_cache(maxsize=1)
def stats() -> dict | None:
    d = db()
    if d is None:
        return None
    with _lock:
        n_docs = d.execute("select count(*) c from docs").fetchone()["c"]
        n_chunks = d.execute("select count(*) c from chunks").fetchone()["c"]
        agg = d.execute("select count(distinct domain) dom, count(distinct root) rt, "
                        "count(distinct job) jb, sum(bytes) b, "
                        "count(url) u, count(title) t from docs").fetchone()
        dom = d.execute("select domain k, count(*) n from docs where domain <> '' "
                        "group by 1 order by n desc").fetchall()
        # `en-US` và `en` là một thứ tiếng — gộp về mã gốc trước khi đếm
        lang = d.execute(
            "select substr(lang, 1, case when instr(lang,'-')>0 then instr(lang,'-')-1 "
            "else length(lang) end) k, count(*) n from docs where lang <> '' "
            "group by 1 order by n desc").fetchall()
        job = d.execute("select job k, count(*) n from docs where job <> '' "
                        "group by 1 order by n desc").fetchall()
        root = d.execute("select root k, count(*) n from docs group by 1 "
                         "order by n desc").fetchall()
    return {"n_docs": n_docs, "n_chunks": n_chunks, "n_domains": agg["dom"],
            "n_roots": agg["rt"], "n_jobs": agg["jb"], "n_langs": len(lang),
            "mb": round((agg["b"] or 0) / 1e6, 1),
            "with_url": agg["u"], "with_title": agg["t"],
            "roots": [{"k": ROOT_VI.get(r["k"], r["k"]), "n": r["n"]} for r in root],
            "domains": _top(dom, 12), "langs": _top(lang, 10), "jobs": _top(job, 10)}


def search(q: str, limit: int, offset: int) -> dict:
    d = db()
    if d is None:
        return {"total": 0, "limit": limit, "offset": offset, "rows": [],
                "note": "không mở được chỉ mục tài liệu"}
    table = "fts_cjk" if CJK.search(q) else "fts"
    match = _phrase(q)
    sql_rows = f"""
        select d.id as doc_id, d.title, d.domain, d.lang, d.url, d.root,
               d.job, d.bytes, c.n as chunk_no
        from {table} f
        join chunks c on c.cid = f.rowid
        join docs d on d.id = c.doc_id
        where {table} match ?
        order by bm25({table}) limit ? offset ?"""
    try:
        with _lock:
            rows = [dict(r) for r in d.execute(sql_rows, (match, limit, offset))]
            total = d.execute(
                f"select count(*) c from {table} where {table} match ?",
                (match,)).fetchone()["c"]
    except sqlite3.OperationalError as e:
        return {"total": 0, "limit": limit, "offset": offset, "rows": [],
                "error": f"chỉ mục từ chối truy vấn: {e}"}
    return {"total": total, "limit": limit, "offset": offset, "rows": rows,
            "table": table,
            "note": "Chỉ mục là FTS5 `content=''` nên KHÔNG lưu lại nguyên văn — "
                    "không trích được đoạn văn quanh từ khoá. Muốn có đoạn trích "
                    "phải đọc file .md theo `chunks.off/len`, chưa nối."}
