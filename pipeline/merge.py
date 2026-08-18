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


def split_threads(files: List[Path], threads: int) -> List[List[Path]]:
    """Chia đều danh sách toà cho N thread, theo thứ tự building_id đã sắp xếp."""
    threads = max(1, threads)
    return [files[i::threads] for i in range(threads)] if files else []


def run(csv_dir: Optional[Path] = None, *, threads: int = 1, day: str = "",
        drop_evidence: bool = False, clean: bool = True,
        quiet: bool = False) -> Dict[str, Path]:
    """Ghi thread<N>_<ngày>.csv + _benchmark.csv. Trả {tên file: đường dẫn}."""
    csv_dir = csv_dir or config.CSV_DIR
    files = building_files(csv_dir)
    if not files:
        raise SystemExit(f"Không có CSV toà nhà nào trong {csv_dir}")
    day = day or time.strftime("%Y%m%d")

    cols = writer.META + writer.feature_columns() + writer.EVIDENCE
    if drop_evidence:
        # evidence_json chiếm ~85% dung lượng; bảng dùng để phân tích thường không
        # cần nó inline — vẫn tra được ở CSV từng toà.
        cols = [c for c in cols if c != "evidence_json"]

    written: Dict[str, Path] = {}
    rows: List[Dict[str, Any]] = []
    for i, chunk in enumerate(split_threads(files, threads), 1):
        if not chunk:
            continue
        part = sorted((r for p in chunk for r in _rows_of(p)), key=_order)
        rows += part
        path = csv_dir / f"thread{i}_{day}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            wr.writeheader()
            wr.writerows(part)
        written[path.name] = path
        if not quiet:
            print(f"      → {path.name}: {len(chunk)} toà · {len(part):,} dòng "
                  f"· {path.stat().st_size / 1024 / 1024:.1f} MB")

    if clean:
        for old_path in stale_thread_files(csv_dir, set(written.values())):
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
