"""Đối chiếu feature_spec.md ↔ schema.py.

`schema.py` là bản dịch tay của spec sang JSON Schema: agent đọc spec để hiểu ý
nghĩa từng trường, nhưng BỘ TRƯỜNG được phép trả về thì do schema.py định nghĩa.
Sửa spec mà quên sửa schema.py → trường mới bị bỏ im lặng. Module này bắt đúng
tình huống đó.

    python3 run.py --check-spec        # thoát mã 1 nếu có chênh lệch

Đọc được: bảng markdown trong mục 1–8 (cột đầu là tên trường trong dấu backtick)
và danh mục giá trị §9. Không đọc: văn xuôi §10–§14 — phần đó agent đọc trực tiếp.
"""
from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from . import config, schema

# `## 3. B2 — `unit_type`` → B2 ; mục 2 là khối kiến trúc §2.2 vẫn thuộc B1
HEAD_RE = re.compile(r"^##\s+(\d+)\.\s+(B[1-7])\b")
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VOCAB_RE = re.compile(r"^\*\*§(8\.\d+)\s+`([^`]+)`")

LABEL_TO_TABLE = {t.label: name for name, t in schema.TABLES.items()}

# §9 dùng số mục để định danh danh mục; hai mục trùng tên `source_type`/`location`
SECTION_TO_VOCAB = {
    "8.1": "building_type", "8.2": "segment", "8.3": "status", "8.4": "handover_standard",
    "8.5": "layout_class", "8.6": "massing_form", "8.7": "facade_system", "8.8": "balcony_type",
    "8.9": "green_cert", "8.10": "area_basis", "8.11": "room_type", "8.12": "room_source",
    "8.13": "floor_label", "8.14": "corridor_type", "8.15": "item_category",
    "8.16": "amenity_category", "8.17": "amenity_location", "8.18": "price_source",
}

# Trường spec khai trong bảng nhưng thuộc cột tổng hợp của dòng B1 (§8.1 phái sinh)
SUMMARY_FIELDS = set(schema.BENCHMARK_EXTRA)


def _clean(cell: str) -> str:
    return cell.replace("**", "").replace("`", "").strip()


def parse_spec(text: str) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """→ ({tên bảng: [trường]}, {tên danh mục: [giá trị]})"""
    fields: Dict[str, List[str]] = {name: [] for name in schema.TABLES}
    vocab: Dict[str, List[str]] = {}
    current: str = ""

    for line in text.splitlines():
        m = HEAD_RE.match(line)
        if m:
            current = LABEL_TO_TABLE.get(m.group(2), "")
            continue
        if line.startswith("## "):                      # mục không gắn bảng nào
            if not line.startswith("## 2."):            # §2 vẫn là khối §2.2 của B1
                current = ""
            continue

        v = VOCAB_RE.match(line)
        if v:
            key = SECTION_TO_VOCAB.get(v.group(1))
            if key:
                # Giá trị nằm SAU phần in đậm; trong `**§8.10 `area_basis` / `price_basis`**`
                # hai token đầu là tên gọi của danh mục, không phải giá trị.
                tail = line.split("**", 2)[-1]
                vocab[key] = [_clean(x) for x in re.findall(r"`([^`]+)`", tail)]
            continue

        if current and line.startswith("|"):
            first = _clean(line.strip().strip("|").split("|")[0])
            if first == "name":                          # dòng tiêu đề của bảng spec
                continue
            if FIELD_RE.match(first) and first not in fields[current]:
                fields[current].append(first)
    return fields, vocab


def check() -> List[str]:
    spec_fields, spec_vocab = parse_spec(config.read_spec())
    problems: List[str] = []
    all_code_fields: Set[str] = {c for t in schema.TABLES.values() for c in t.columns} | SUMMARY_FIELDS

    print("Đối chiếu feature_spec.md ↔ schema.py\n")
    print(f"  {'Bảng':<22} {'spec':>5} {'code':>5}   chênh lệch")
    for name, t in schema.TABLES.items():
        sp, code = spec_fields[name], set(t.columns)
        missing = [f for f in sp if f not in code and f not in SUMMARY_FIELDS]
        elsewhere = [f for f in sp if f not in code and f in SUMMARY_FIELDS]
        extra = [c for c in t.columns if c not in sp]
        note = "✓" if not missing else f"THIẾU {len(missing)}"
        print(f"  {t.label + ' ' + name:<22} {len(sp):>5} {len(t.columns):>5}   {note}")
        for f in missing:
            problems.append(f"[THIẾU] {t.label} {name}: spec khai `{f}` nhưng schema.py không có "
                            f"→ trường này sẽ bị bỏ im lặng")
        for f in elsewhere:
            print(f"      · `{f}` nằm ở cột tổng hợp dòng B1 (BENCHMARK_EXTRA) — OK")
        if extra:
            named = [c for c in extra if c not in all_code_fields or c in t.derived]
            print(f"      · {len(extra)} cột code có thêm (khoá/FK/derived): "
                  f"{', '.join(extra[:6])}{'…' if len(extra) > 6 else ''}")
            for c in named:
                if c not in t.derived and not c.endswith("_id"):
                    problems.append(f"[THỪA] {t.label} {name}: schema.py có `{c}` nhưng spec "
                                    f"không khai — kiểm lại tên trường")

    print(f"\n  {'Danh mục §9':<22} {'spec':>5} {'code':>5}   chênh lệch")
    for key, code_vals in schema.V.items():
        if key not in spec_vocab:
            if key not in ("confidence", "market", "currency", "price_unit"):
                problems.append(f"[VOCAB] `{key}` có trong schema.py nhưng không thấy ở §9")
            continue
        sp = spec_vocab[key]
        add = [v for v in sp if v not in code_vals]
        rm = [v for v in code_vals if v not in sp]
        note = "✓" if not (add or rm) else f"lệch {len(add) + len(rm)}"
        print(f"  §9 {key:<19} {len(sp):>5} {len(code_vals):>5}   {note}")
        for v in add:
            problems.append(f"[VOCAB] `{key}`: spec có giá trị `{v}` mà schema.py thiếu "
                            f"→ model không được phép trả giá trị này")
        for v in rm:
            problems.append(f"[VOCAB] `{key}`: schema.py có `{v}` mà spec §9 không khai")
    return problems
