"""Gộp output_csv/<building_id>.csv thành file tổng hợp toàn danh sách.

Vì sao vẫn ghi rời từng toà rồi mới gộp, thay vì ghi thẳng một file:
  · 8 tiến trình chạy song song cùng nối vào một file là hỏng file — ghi rời thì
    mỗi tiến trình sở hữu đúng một đường dẫn, không cần khoá.
  · Chạy lại một toà chỉ đụng vào file của toà đó; mẻ đứt giữa chừng không để lại
    file tổng hợp cụt đầu cụt đuôi.
  · Gộp lại tốn chưa tới một giây, làm bao nhiêu lần cũng được.

Ra `thread<N>_<YYYYMMDD>.csv` — mỗi thread một file, ngày là ngày sinh file. Toà
được chia đều theo building_id đã sắp xếp, nên chạy lại cho ra đúng cách chia cũ.
Kèm `_benchmark.csv`: đúng 1 dòng mỗi toà (B1 + cột tổng hợp) để so sánh nhanh.

Gộp đọc lại CSV từng toà đã có sẵn — KHÔNG chạy lại bước trích.
"""
from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import config, schema, writer

BENCH_NAME = "_benchmark.csv"
# Nhận diện file đúng định dạng — thư mục output_csv/ có thể chứa CSV của công cụ
# khác (vd file_lan.csv của code_ui) mà gộp nhầm vào là hỏng bảng.
SIGNATURE = ("bang", "record_key")
# File do CHÍNH bước gộp sinh ra. Phải loại khỏi đầu vào: chúng cùng định dạng với
# CSV từng toà, nên không loại là lần gộp sau ăn lại output lần trước và mọi toà
# bị nhân đôi.
THREAD_RE = re.compile(r"^thread\d+_\d{8}\.csv$")

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))    # evidence_json có ô rất dài


def building_files(csv_dir: Optional[Path] = None) -> List[Path]:
    """CSV của từng toà, bỏ file tổng hợp và file lạ định dạng."""
    csv_dir = csv_dir or config.CSV_DIR
    out = []
    for path in sorted(csv_dir.glob("*.csv")):
        if path.name.startswith("_") or THREAD_RE.match(path.name):
            continue
        try:
            with path.open(encoding="utf-8-sig", newline="") as f:
                header = next(csv.reader(f), [])
        except OSError:
            continue
        if all(col in header for col in SIGNATURE):
            out.append(path)
    return out


def _rows_of(path: Path) -> Iterator[Dict[str, Any]]:
    """Dòng của một toà, đã bù building_id.

    B3 `unit_room` không có cột building_id trong schema (khoá của nó là
    unit_type_id), nên khi trộn 209 toà vào một bảng thì những dòng đó mất dấu
    toà. Tên file chính là building_id — lấy từ đó, chắc chắn đúng.
    """
    bid = path.stem
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row["building_id"] = row.get("building_id") or bid
            yield row


def _order(row: Dict[str, Any]) -> Tuple:
    labels = [schema.TABLES[n].label for n in writer.TABLE_ORDER]
    label = str(row.get("bang") or "")
    return (str(row.get("building_id") or ""),
            labels.index(label) if label in labels else len(labels))


def stale_thread_files(csv_dir: Path, keep: set) -> List[Path]:
    """File thread của lần gộp TRƯỚC — số thread hoặc ngày khác lần này.

    Không dọn thì thư mục lẫn lộn nhiều bản, UI đọc cả bản cũ lẫn mới và một toà
    hiện lên hai lần.
    """
    return [p for p in csv_dir.glob("thread*_*.csv")
            if THREAD_RE.match(p.name) and p not in keep]


def split_threads(items: List[Any], threads: int) -> List[List[Any]]:
    """Chia đều danh sách toà cho N thread, theo thứ tự building_id đã sắp xếp."""
    threads = max(1, threads)
    return [items[i::threads] for i in range(threads)] if items else []


def read_thread_file(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Dòng + tên cột của một file thread đã có. Cột trả về để không đánh rơi
    cột lạ khi ghi đè (vd bản cũ có evidence_json mà lần này chạy --no-evidence)."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return list(rd), list(rd.fieldnames or [])


def carry_rows(csv_dir: Path, day: str, skip_ids: set) -> Tuple[Dict[str, List[Dict]], List[str]]:
    """Đọc lại file thread CÙNG NGÀY, giữ dòng của toà lần này KHÔNG ghi lại.

    Dùng cho chế độ append. Toà nào có CSV riêng trong output_csv/ thì lần gộp này
    dựng lại từ đó (bản mới thắng), nên loại khỏi phần mang sang — nếu không mỗi
    toà sẽ nằm hai lần trong file. Gom theo building_id chứ không theo file cũ, vì
    số thread có thể đổi giữa hai lần chạy và toà sẽ rơi sang file khác.
    """
    carried: Dict[str, List[Dict[str, Any]]] = {}
    extra_cols: List[str] = []
    for path in sorted(csv_dir.glob(f"thread*_{day}.csv")):
        if not THREAD_RE.match(path.name):
            continue
        rows, cols = read_thread_file(path)
        for col in cols:
            if col not in extra_cols:
                extra_cols.append(col)
        for row in rows:
            bid = str(row.get("building_id") or "").strip()
            if bid and bid not in skip_ids:
                carried.setdefault(bid, []).append(row)
    return carried, extra_cols


def run(csv_dir: Optional[Path] = None, *, threads: int = 1, day: str = "",
        drop_evidence: bool = False, clean: bool = True,
        quiet: bool = False, append: bool = False) -> Dict[str, Path]:
    """Ghi thread<N>_<ngày>.csv + _benchmark.csv. Trả {tên file: đường dẫn}.

    `append=False` (mặc định): dựng lại file thread từ đầu, chỉ phản ánh những gì
    đang có trong output_csv/. Xoá CSV một toà là toà đó biến mất khỏi bảng gộp.

    `append=True`: file thread CÙNG NGÀY đã có thì gộp thêm vào, chưa có thì tạo
    mới. Toà đang có CSV riêng vẫn được dựng lại từ CSV đó (bản mới đè bản cũ,
    không nhân đôi); toà chỉ còn trong file thread cũ thì được mang sang nguyên
    vẹn. Nhờ vậy file thread thành sổ tích luỹ của cả ngày, chạy bao nhiêu mẻ
    cũng cộng dồn.
    """
    csv_dir = csv_dir or config.CSV_DIR
    files = building_files(csv_dir)
    day = day or time.strftime("%Y%m%d")

    cols = writer.META + writer.feature_columns() + writer.EVIDENCE
    if drop_evidence:
        # evidence_json chiếm ~85% dung lượng; bảng dùng để phân tích thường không
        # cần nó inline — vẫn tra được ở CSV từng toà.
        cols = [c for c in cols if c != "evidence_json"]

    # Đơn vị chia thread: (building_id, cách lấy dòng). Toà có CSV riêng thì đọc
    # từ CSV; toà mang sang từ mẻ trước thì đã có sẵn dòng trong bộ nhớ.
    units: List[Tuple[str, Any]] = [(p.stem, p) for p in files]
    n_carried = 0
    if append:
        carried, old_cols = carry_rows(csv_dir, day, {p.stem for p in files})
        units += [(bid, rows) for bid, rows in carried.items()]
        n_carried = len(carried)
        # Bản cũ có cột lạ (schema đổi giữa hai lần chạy) → giữ lại ở cuối thay vì
        # đánh rơi dữ liệu. Trừ cột NGƯỜI DÙNG CHỦ ĐỘNG bỏ: --no-evidence mà vẫn
        # khôi phục evidence_json thì cờ đó thành vô nghĩa.
        dropped = {"evidence_json"} if drop_evidence else set()
        extra = [c for c in old_cols if c not in cols and c not in dropped]
        if extra:
            cols = cols + extra
            if not quiet:
                print(f"      · giữ {len(extra)} cột chỉ có ở bản cũ: {', '.join(extra)}")
    if not units:
        raise SystemExit(f"Không có CSV toà nhà nào trong {csv_dir}")
    units.sort(key=lambda u: u[0])

    written: Dict[str, Path] = {}
    rows: List[Dict[str, Any]] = []
    for i, chunk in enumerate(split_threads(units, threads), 1):
        if not chunk:
            continue
        part = sorted((r for _, src in chunk
                       for r in (_rows_of(src) if isinstance(src, Path) else src)),
                      key=_order)
        rows += part
        path = csv_dir / f"thread{i}_{day}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", restval="")
            wr.writeheader()
            wr.writerows(part)
        written[path.name] = path
        if not quiet:
            print(f"      → {path.name}: {len(chunk)} toà · {len(part):,} dòng "
                  f"· {path.stat().st_size / 1024 / 1024:.1f} MB")
    if append and not quiet and n_carried:
        print(f"      · mang sang {n_carried} toà chỉ còn trong bản gộp cũ")

    if clean:
        stale = stale_thread_files(csv_dir, set(written.values()))
        if append:
            # Chỉ dọn file THỪA của chính ngày này (số thread đổi nên dôi ra) —
            # nội dung của chúng đã được mang sang file mới. File của ngày khác là
            # lịch sử, append không có quyền xoá.
            stale = [p for p in stale if p.name.endswith(f"_{day}.csv")]
        for old_path in stale:
            old_path.unlink()
            if not quiet:
                print(f"      ✗ dọn bản cũ {old_path.name}")

    rows.sort(key=_order)

    # ── Bảng benchmark: đúng 1 dòng mỗi toà ─────────────────────────────────
    b1 = [r for r in rows if r.get("bang") == schema.TABLES["building"].label]
    if b1:
        keep = [c for c in cols
                if c not in ("bang", "bang_ten", "record_key", "evidence_json")
                and any(str(r.get(c) or "").strip() for r in b1)]  # bỏ cột rỗng trơn
        bench_path = csv_dir / BENCH_NAME
        with bench_path.open("w", encoding="utf-8-sig", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=keep, extrasaction="ignore")
            wr.writeheader()
            wr.writerows(b1)
        written[BENCH_NAME] = bench_path

    if not quiet and BENCH_NAME in written:
        print(f"      → {BENCH_NAME}: {len(b1)} dòng (1 dòng/toà) × {len(keep)} cột")
    return written
