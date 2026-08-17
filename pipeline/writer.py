"""Ghi output_csv/<building_id>.csv — mỗi toà nhà đúng một file (feature_spec §14.1).

1 dòng = 1 record của một trong 7 bảng; cột `bang` phân biệt bảng, cột feature là
hợp của cả 7 bảng, ba cột cuối mang evidence:

    confidence      mức thấp nhất trong các trường của record
    source_urls     các URL nguồn của record
    evidence_json   {"<field>": {"url","file","snippet","confidence"}} — tra từng trường

Ghi bằng UTF-8 có BOM để mở thẳng bằng Excel không vỡ tiếng Việt.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from . import config, schema

Rows = List[Dict[str, Any]]

META = ["bang", "bang_ten", "record_key", "record_label"]
EVIDENCE = ["confidence", "source_urls", "evidence_json"]
TABLE_ORDER = ["building", "unit_type", "unit_room", "floor_plate",
               "handover_item", "amenity", "price_obs"]
CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def feature_columns() -> List[str]:
    cols: List[str] = []
    for name in TABLE_ORDER:
        for c in schema.TABLES[name].columns:
            if c not in cols:
                cols.append(c)
    for c in schema.BENCHMARK_EXTRA:                 # cột tổng hợp, chỉ dòng B1 có
        if c not in cols:
            cols.append(c)
    return cols


def _label(table: str, r: Dict[str, Any], type_of: Dict[str, str]) -> str:
    if table == "building":
        return r.get("building_name") or r.get("building_id") or ""
    if table == "unit_type":
        return " · ".join(x for x in (r.get("type_code"), r.get("type_name")) if x)
    if table == "unit_room":
        return " · ".join(x for x in (type_of.get(r.get("unit_type_id"), ""),
                                      r.get("room_label_raw") or r.get("room_code")) if x)
    if table == "floor_plate":
        return " · ".join(x for x in (r.get("tower_code"), f"tầng {r.get('floor_range')}"
                                      if r.get("floor_range") else None) if x)
    if table == "handover_item":
        return r.get("item_name") or r.get("item_code") or ""
    if table == "amenity":
        return r.get("amenity_name") or ""
    if table == "price_obs":
        return " · ".join(x for x in (type_of.get(r.get("unit_type_id"), "toàn toà"),
                                      r.get("market"), r.get("period")) if x)
    return ""


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "; ".join("" if x is None else str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def build_rows(tables: Dict[str, Rows], provenance: Rows, summary: Dict[str, Any]) -> Rows:
    ev: Dict[tuple, Dict[str, Any]] = {}
    for p in provenance:
        ev.setdefault((p["table"], p["record_key"]), {})[p["field"]] = {
            "url": p.get("source_url") or "", "file": p.get("source_file") or "",
            "snippet": p.get("snippet") or "", "confidence": p.get("confidence") or "",
        }
    type_of = {r["unit_type_id"]: (r.get("type_code") or "") for r in tables["unit_type"]}
    key_of = {"building": "building_id", "unit_type": "unit_type_id", "unit_room": "room_id",
              "floor_plate": "floor_plate_id", "handover_item": "handover_id",
              "amenity": "amenity_id", "price_obs": "price_id"}

    out: Rows = []
    for name in TABLE_ORDER:
        t = schema.TABLES[name]
        for r in tables[name]:
            key = r.get(key_of[name]) or ""
            fields = ev.get((name, key), {})
            confs = [f["confidence"] for f in fields.values() if f.get("confidence")]
            urls = sorted({f["url"] for f in fields.values() if f.get("url")})
            row = {"bang": t.label, "bang_ten": name, "record_key": key,
                   "record_label": _label(name, r, type_of), **r,
                   "confidence": min(confs, key=lambda c: CONF_RANK.get(c, 0)) if confs else "",
                   "source_urls": "; ".join(urls),
                   "evidence_json": json.dumps(fields, ensure_ascii=False) if fields else ""}
            if name == "building":
                row.update({k: summary.get(k) for k in schema.BENCHMARK_EXTRA})
            out.append(row)
    return out


def run(building_id: str, tables: Dict[str, Rows], provenance: Rows, benchmark: Rows,
        warnings: List[str], out_raw: Path) -> Path:
    print("[4/4] Ghi output_csv/")
    config.CSV_DIR.mkdir(parents=True, exist_ok=True)
    rows = build_rows(tables, provenance, benchmark[0] if benchmark else {})
    cols = META + feature_columns() + EVIDENCE

    path = config.CSV_DIR / f"{building_id}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow({c: _cell(r.get(c)) for c in cols})

    per_table = {schema.TABLES[n].label: len(tables[n]) for n in TABLE_ORDER}
    n_ev = sum(1 for r in rows if r["evidence_json"])
    print(f"      {path.name}: {len(rows)} dòng × {len(cols)} cột · "
          f"{n_ev}/{len(rows)} dòng có evidence")
    print("      " + " · ".join(f"{k}={v}" for k, v in per_table.items()))

    log = out_raw / "validation.log"
    log.write_text("\n".join(warnings) + ("\n" if warnings else ""), encoding="utf-8")
    if warnings:
        print(f"\n      ⚠ {len(warnings)} cảnh báo kiểm tra chéo (đã ghi {log}):")
        for line in warnings[:12]:
            print(f"        - {line}")
        if len(warnings) > 12:
            print(f"        … còn {len(warnings) - 12} dòng trong validation.log")
    else:
        print("      ✓ không có cảnh báo kiểm tra chéo")
    return path
