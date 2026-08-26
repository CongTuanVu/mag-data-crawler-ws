"""Slug ASCII cho các trường định danh tiếng Việt.

Vì sao cần: `?province=Hà Nội` bắt frontend phải percent-encode đúng, và sai một
nhịp là 400 khó truy. Định danh thì nên là ASCII ổn định; chữ có dấu chỉ dùng để
HIỂN THỊ, và API trả kèm cả hai.

Cái bẫy: `strip_accents` của DuckDB (và `unicodedata` của Python) chỉ bỏ dấu
thanh, KHÔNG đổi `đ` → `d`:

    strip_accents('đường Đông')  ->  'đuong Đong'

Không xử lý riêng thì `da-nang` không khớp `Đà Nẵng`, `dong-nai` không khớp
`Đồng Nai`. Nên có bước thay `đ`/`Đ` tường minh dưới đây.
"""
from __future__ import annotations

import re
import unicodedata


def slugify(s: str) -> str:
    """`Hà Nội` → `ha-noi` · `Đồng Nai` → `dong-nai` · `Nhà phố / biệt thự` → `nha-pho-biet-thu`"""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D")      # NFD không tách được chữ này
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return re.sub(r"-{2,}", "-", s)


def index(values: list[str]) -> dict[str, str]:
    """Bảng tra slug → giá trị gốc.

    Hai giá trị khác nhau có thể cho cùng một slug (ví dụ `Bà Rịa – Vũng Tàu` và
    `Bà Rịa Vũng Tàu`). Khi đó giá trị ĐẦU TIÊN theo thứ tự truyền vào giữ slug
    trần, các giá trị sau nhận hậu tố `-2`, `-3`… — cố định, không phụ thuộc lần
    chạy, để đường dẫn không đổi giữa hai lần khởi động.
    """
    out: dict[str, str] = {}
    for v in values:
        if not v:
            continue
        base = slugify(v)
        if not base:
            continue
        k, i = base, 1
        while k in out and out[k] != v:
            i += 1
            k = f"{base}-{i}"
        out[k] = v
    return out


def resolve(table: dict[str, str], key: str | None) -> str | None:
    """Nhận slug, trả giá trị gốc. Nhận luôn cả chữ có dấu để không phá client cũ."""
    if not key:
        return None
    if key in table:
        return table[key]
    s = slugify(key)
    if s in table:
        return table[s]
    for v in table.values():                       # client cũ gửi thẳng nhãn
        if v == key:
            return v
    return None
