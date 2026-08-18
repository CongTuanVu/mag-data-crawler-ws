"""Chạy code bóc tách (do agent sinh) trên raw của một toà → extract_text.json.

Runner VIẾT TAY, không bị agent ghi đè. Nhiệm vụ: nạp `rules.py` + `sites/*.py`,
gọi đúng hợp đồng, rồi CHUẨN HOÁ kết quả về đúng dạng mà `pipeline/assemble.py`
đang chờ — nhờ vậy toàn bộ khâu lắp ráp/kiểm tra/ghi CSV phía sau không đổi.

Hợp đồng với code sinh ra
─────────────────────────
`code_extract/rules.py` khai báo, cho mỗi bảng text (tên hàm = tên bảng):

    def building(pages: list[C.Page], ctx: dict) -> list[dict]
    def unit_type(pages, ctx) -> list[dict]
    def floor_plate(pages, ctx) -> list[dict]
    def handover_item(pages, ctx) -> list[dict]
    def amenity(pages, ctx) -> list[dict]
    def price_obs(pages, ctx) -> list[dict]

`code_extract/sites/<host>.py` (tuỳ chọn) khai báo `HOSTS = ("suumo.jp",)` và
cùng bộ tên hàm nhưng nhận MỘT trang: `def unit_type(page, ctx) -> list[dict]`.
Record của site được ưu tiên khi trùng khoá với record của rules chung.

Mọi hàm được gọi trong try/except: code sinh ra vỡ ở một bảng thì các bảng khác
vẫn ra, kèm cảnh báo — không đánh sập cả mẻ 209 toà.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import pkgutil
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pipeline import schema
from pipeline.extract import normalize_language, residual_cjk

from . import common as C
from . import lexicon

DIR = Path(__file__).resolve().parent
RULES_PATH = DIR / "rules.py"
SITES_DIR = DIR / "sites"

# Trường giữ nguyên văn — không đưa qua từ điển (feature_spec quy tắc 8c).
VERBATIM_KEYS = {"provenance", "snippet", "source_file", "field", "confidence",
                 "floorplan_url", "floorplate_url", "listing_url", "brochure_url"}
VERBATIM_SUFFIXES = ("_local", "_code", "_url", "_id")

# Khoá khử trùng của từng bảng: record trùng khoá thì gộp, không nhân đôi.
DEDUP_KEYS = {
    "unit_type": ("type_code",),
    "floor_plate": ("tower_code", "floor_range"),
    "handover_item": ("item_code", "applies_to_type_code"),
    "amenity": ("slug",),
    "price_obs": ("market", "period", "unit_type_code", "source_type"),
}


class ExtractorMissing(SystemExit):
    pass


# ── Nạp code sinh ra ────────────────────────────────────────────────────────
def load_rules(reload: bool = False):
    if not RULES_PATH.exists():
        raise ExtractorMissing(
            f"chưa có {RULES_PATH} — code bóc tách chưa được sinh.\n"
            f"      Chạy: python run_extract.py build")
    mod = importlib.import_module("code_extract.rules")
    return importlib.reload(mod) if reload else mod


def load_sites(reload: bool = False) -> List[Any]:
    mods = []
    if not SITES_DIR.exists():
        return mods
    for info in pkgutil.iter_modules([str(SITES_DIR)]):
        if info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"code_extract.sites.{info.name}")
            if reload:
                mod = importlib.reload(mod)
        except Exception:
            print(f"      ! bỏ qua sites/{info.name}.py — lỗi import:\n"
                  f"{traceback.format_exc(limit=3)}")
            continue
        if getattr(mod, "HOSTS", None):
            mods.append(mod)
    return mods


def site_for(page: C.Page, mods: List[Any]) -> Optional[Any]:
    for mod in mods:
        for host in mod.HOSTS:
            if page.host == host or page.host.endswith("." + host):
                return mod
    return None


# ── Chuẩn hoá record về đúng schema ─────────────────────────────────────────
def _coerce(value: Any, typ: str, table: str, fname: str, warns: List[str]) -> Any:
    if value is None or value == "":
        return None
    try:
        if typ.startswith("enum:"):
            allowed = schema.V[typ.split(":", 1)[1]]
            s = str(value).strip()
            if s not in allowed:
                warns.append(f"{table}.{fname}: bỏ giá trị ngoài danh mục {s!r}")
                return None
            return s
        if typ == "str":
            return str(value).strip() or None
        if typ == "float":
            return float(value)
        if typ == "int":
            return int(round(float(value)))
        if typ == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("true", "yes", "1", "có")
            return bool(value)
        if typ == "list":
            if isinstance(value, str):
                value = [value]
            return [str(v).strip() for v in value if str(v).strip()] or None
        if typ == "pairs":
            out = []
            if isinstance(value, dict):
                value = [{"key": k, "value": v} for k, v in value.items()]
            for item in value or []:
                if isinstance(item, dict) and "key" in item and "value" in item:
                    out.append({"key": str(item["key"]), "value": float(item["value"])})
            return out or None
    except (TypeError, ValueError):
        warns.append(f"{table}.{fname}: không ép được kiểu {typ} từ {value!r}")
        return None
    return value


def _translate(value: Any, key: Optional[str] = None) -> Any:
    """Đưa trường mô tả qua từ điển JP→VI (quy tắc 8a/8b)."""
    if key is not None and (key in VERBATIM_KEYS or key.endswith(VERBATIM_SUFFIXES)):
        return value
    if isinstance(value, str):
        return lexicon.vi_phrase(value)
    if isinstance(value, list):
        return [_translate(v, key) for v in value]
    if isinstance(value, dict):
        return {k: _translate(v, k) for k, v in value.items()}
    return value


def sanitize(table_name: str, records: List[Dict[str, Any]],
             warns: List[str]) -> List[Dict[str, Any]]:
    """Ép record thô của code sinh ra về đúng trường/kiểu/enum của schema."""
    t = schema.TABLES[table_name]
    fields = {f.name: f.typ for f in t.llm}
    out = []
    for raw in records or []:
        if not isinstance(raw, dict):
            warns.append(f"{table_name}: bỏ record không phải dict ({type(raw).__name__})")
            continue
        body: Dict[str, Any] = {}
        for fname, typ in fields.items():
            body[fname] = _coerce(raw.get(fname), typ, table_name, fname, warns)
        unknown = set(raw) - set(fields) - {"provenance"}
        if unknown:
            warns.append(f"{table_name}: bỏ trường lạ {sorted(unknown)}")

        lines = []
        for p in raw.get("provenance") or []:
            if not isinstance(p, dict):
                continue
            fname = str(p.get("field", "")).strip()
            if fname not in fields or body.get(fname) is None:
                continue                       # spec §5: provenance chỉ cho trường có giá trị
            lines.append({"field": fname,
                          "source_file": str(p.get("source_file", "")).strip(),
                          "snippet": C.squash(p.get("snippet", "")),
                          "confidence": (p.get("confidence")
                                         if p.get("confidence") in schema.V["confidence"]
                                         else "medium")})
        seen, dedup = set(), []
        for line in lines:                     # spec §5: ĐÚNG một dòng mỗi trường
            if line["field"] in seen:
                continue
            seen.add(line["field"])
            dedup.append(line)
        missing = [k for k, v in body.items() if v is not None and k not in seen]
        if missing:
            warns.append(f"{table_name}: {len(missing)} trường thiếu provenance "
                         f"({', '.join(missing[:5])}) — sẽ bị loại ở bước kiểm tra")
        body["provenance"] = dedup
        out.append(body)
    return out


def _key_of(table_name: str, record: Dict[str, Any]) -> Optional[Tuple]:
    keys = DEDUP_KEYS.get(table_name)
    if not keys:
        return None
    return tuple(str(record.get(k) or "") for k in keys)


def merge_records(table_name: str, groups: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Gộp record từ nhiều nguồn: nhóm đầu được ưu tiên khi trùng khoá.

    B1 luôn thu về ĐÚNG 1 record — trường trống của nhóm ưu tiên được nhóm sau
    lấp vào, nên trang CĐT quyết định con số còn cổng rao chỉ bù chỗ thiếu.
    """
    flat = [r for g in groups for r in g]
    if table_name == "building":
        if not flat:
            return []
        merged: Dict[str, Any] = {k: None for k in flat[0] if k != "provenance"}
        prov: List[Dict[str, str]] = []
        taken = set()
        for r in flat:
            by_field = {p["field"]: p for p in r.get("provenance", [])}
            for k, v in r.items():
                if k == "provenance" or v is None or merged.get(k) is not None:
                    continue
                merged[k] = v
                if k in by_field and k not in taken:
                    prov.append(by_field[k])
                    taken.add(k)
        merged["provenance"] = prov
        return [merged]

    out: List[Dict[str, Any]] = []
    index: Dict[Tuple, int] = {}
    for r in flat:
        key = _key_of(table_name, r)
        if key is None or key not in index:
            if key is not None:
                index[key] = len(out)
            out.append(r)
            continue
        keep = out[index[key]]                 # đã có: chỉ lấp trường còn trống
        by_field = {p["field"]: p for p in r.get("provenance", [])}
        have = {p["field"] for p in keep.get("provenance", [])}
        for k, v in r.items():
            if k == "provenance" or v is None or keep.get(k) is not None:
                continue
            keep[k] = v
            if k in by_field and k not in have:
                keep.setdefault("provenance", []).append(by_field[k])
                have.add(k)
    return out


# ── Chạy một toà ────────────────────────────────────────────────────────────
def _call(fn, warns: List[str], *args) -> List[Dict[str, Any]]:
    """Gọi một hàm của code sinh ra. Nhận cả `list` lẫn `{"records", "notes"}`.

    Dạng dict cho phép code giải thích VÌ SAO bảng rỗng (vd nguồn chỉ công bố dải
    diện tích, không có bảng từng loại căn) — ghi chú đó đi thẳng vào notes của
    extract_text.json nên người vận hành đọc được, thay vì phải đoán.
    """
    result = fn(*args)
    if result is None:
        return []
    if isinstance(result, dict):
        note = str(result.get("notes") or "").strip()
        if note:
            warns.append(note)
        return list(result.get("records") or [])
    return list(result)


def run(out_dir: Path, manifest: Dict[str, Any], resolved: Dict[str, Any], *,
        building_id: str = "", rules=None, sites=None,
        write: bool = True, quiet: bool = False) -> Dict[str, Any]:
    """Bản thay thế `pipeline.extract.run` — cùng đầu vào, cùng đầu ra."""
    rules = rules or load_rules()
    sites = load_sites() if sites is None else sites
    pages = C.load_pages(out_dir, manifest)
    if not pages:
        raise SystemExit("Corpus rỗng — không có trang nào crawl thành công.")
    ctx = {"building_id": building_id or manifest.get("building_id", ""),
           "resolved": resolved, "manifest": manifest, "out_dir": out_dir,
           "pages": pages}

    if not quiet:
        print(f"[3/4] Bóc tách bằng code ({len(pages)} trang, không gọi LLM)")
    out: Dict[str, Any] = {}
    all_warns: List[str] = []
    for name in schema.TEXT_TABLES:
        warns: List[str] = []
        site_records: List[Dict[str, Any]] = []
        for page in pages:
            mod = site_for(page, sites)
            fn = getattr(mod, name, None) if mod else None
            if not fn:
                continue
            try:
                site_records += _call(fn, warns, page, ctx)
            except Exception:
                warns.append(f"sites/{mod.__name__.split('.')[-1]}.{name} lỗi trên {page.name}: "
                             f"{traceback.format_exc(limit=2).splitlines()[-1]}")
        generic: List[Dict[str, Any]] = []
        fn = getattr(rules, name, None)
        if fn:
            try:
                generic = _call(fn, warns, pages, ctx)
            except Exception:
                warns.append(f"rules.{name} lỗi: {traceback.format_exc(limit=3)}")
        else:
            warns.append(f"rules.py thiếu hàm {name}()")

        records = merge_records(name, [sanitize(name, site_records, warns),
                                       sanitize(name, generic, warns)])
        records = [_translate(r) for r in records]
        records = normalize_language(records)
        notes = "; ".join(dict.fromkeys(warns))[:2000]
        out[name] = {"records": records, "notes": notes}
        all_warns += warns
        if not quiet:
            t = schema.TABLES[name]
            print(f"      {t.label} {name}: {len(records)} record"
                  + (f" · {notes[:110]}" if notes else ""))

    leftover = residual_cjk(out)
    if leftover and not quiet:
        print(f"      ! {leftover} ô còn chữ nguồn chưa dịch — chạy "
              f"`python run_extract.py translate` để bổ sung từ điển")
    lexicon.flush_misses()
    if write:
        (out_dir / "extract_text.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
