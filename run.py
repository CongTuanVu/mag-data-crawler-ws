#!/usr/bin/env python3
"""WS1 Building — pipeline agent: tên toà nhà → feature CSV.

    python3 run.py "Marina One Residences, Singapore"
    python3 run.py --input buildings.txt --max-floorplans 12
    python3 run.py "Songdo Central Park I-Park" --linked-case-id incheon_songdo

Bốn bước: [1] agent tìm nguồn trên mạng → [2] crawl raw về output_raw/
→ [3] trích feature (text + vision bản vẽ) → [4] kiểm tra chéo & ghi output_csv/.

Chạy lại an toàn: raw append-only, mỗi bước ghi kết quả trung gian trong
output_raw/<building_id>/ nên có thể bỏ qua bước đã xong bằng --skip-*.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path
from typing import List

import anthropic

from pipeline import (assemble, config, crawl, discover, extract, floorplan, speccheck,
                      validate, writer)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


SEP_RE = re.compile(r"^:?-{2,}:?$")
NAME_HINTS = ("toà nhà", "tòa nhà", "building", "tên", "name", "project", "dự án")
CITY_HINTS = ("thành phố", "thanh pho", "city", "địa điểm", "quốc gia", "country")


def _clean_cell(c: str) -> str:
    c = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", c)      # [tên](url) -> tên
    return c.replace("**", "").replace("`", "").strip()


def parse_buildings(path: Path) -> List[str]:
    """Đọc danh sách toà nhà: mỗi dòng 1 tên, HOẶC một bảng markdown.

    Với bảng markdown, cột tên lấy theo tiêu đề khớp 'Toà nhà'/'Building'/'Tên',
    cột thành phố khớp 'Thành phố'/'City' được ghép vào truy vấn cho đỡ nhầm toà.
    """
    lines = [ln.rstrip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    table = [ln for ln in lines if ln.strip().startswith("|")]

    if len(table) < 2:                                  # dạng phẳng: mỗi dòng 1 tên
        return [ln.strip() for ln in lines if not ln.lstrip().startswith("#")]

    rows = []
    for ln in table:
        cells = [_clean_cell(c) for c in ln.strip().strip("|").split("|")]
        if cells and all(SEP_RE.match(c) for c in cells if c):
            continue
        rows.append(cells)
    if not rows:
        return []

    header = [c.lower() for c in rows[0]]
    name_i = next((i for i, h in enumerate(header) if any(k in h for k in NAME_HINTS)), None)
    city_i = next((i for i, h in enumerate(header) if any(k in h for k in CITY_HINTS)), None)
    if name_i is None:                                  # không có tiêu đề nhận ra được
        name_i, city_i, rows = (1 if len(rows[0]) > 1 else 0), None, rows
        print("      ! không nhận ra cột tên trong bảng — dùng cột thứ 2")
    else:
        rows = rows[1:]

    out = []
    for cells in rows:
        if name_i >= len(cells) or not cells[name_i]:
            continue
        name = cells[name_i]
        city = cells[city_i] if (city_i is not None and city_i < len(cells)) else ""
        out.append(f"{name}, {city}" if city and city.lower() not in name.lower() else name)
    return out


URL_RE = re.compile(r"https?://[^\s|)\]]+")


def load_sources_file(path: Path, query: str) -> dict:
    """Bảng nguồn tự cấp, thay cho bước agent tìm nguồn.

    Mỗi dòng: `URL [| purpose [| ghi chú]]`, hoặc `[Tên](URL) | purpose`.
    purpose nên là một trong discover.PURPOSES để crawler biết trang nào cần
    render ảnh mặt bằng; bỏ trống thì mặc định official_overview.
    """
    sources = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = URL_RE.search(line)
        if not m:
            continue
        cells = [c.strip() for c in line.split("|")]
        purpose = next((c for c in cells if c in discover.PURPOSES), "official_overview")
        title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cells[0])
        title = URL_RE.sub("", title).strip(" |[]") or f"nguồn {i}"
        sources.append({"url": m.group(0), "title": title, "purpose": purpose,
                        "expected_content": "người dùng tự cấp", "is_official": True,
                        "language": "", "priority": 1})
    if not sources:
        raise SystemExit(f"Không tách được URL nào từ {path}")
    print(f"[1/4] Dùng bảng nguồn tự cấp {path}: {len(sources)} URL")
    return {"resolved": {"found": True, "building_name": query, "building_name_local": None,
                         "project_name": None, "country": "", "city": "",
                         "official_website": None, "developer": None,
                         "building_id_suggestion": config.slugify(query, "building"),
                         "disambiguation_note": f"nguồn do người dùng chỉ định trong {path.name}",
                         "search_languages": []},
            "sources": sources, "gaps": "", "query": query}


def process(query: str, args: argparse.Namespace) -> None:
    print("\n" + "═" * 78)
    print(f"  {query}")
    print("═" * 78)

    # ── [1] nguồn ───────────────────────────────────────────────────────────
    tmp_dir = config.RAW_DIR / config.slugify(query, "building")
    cached_sources = None
    if args.skip_discover:
        for cand in sorted(config.RAW_DIR.glob("*/sources.json")):
            data = json.loads(cand.read_text(encoding="utf-8"))
            if data.get("query") == query:
                cached_sources, tmp_dir = data, cand.parent
                print(f"[1/4] Dùng lại sources.json có sẵn: {cand}")
                break
    if args.sources:
        result = load_sources_file(args.sources, query)
    else:
        result = cached_sources or discover.run(query, tmp_dir)
    resolved = result["resolved"]

    bid = discover.building_id(resolved, args.building_id)
    out_dir = config.RAW_DIR / bid
    if tmp_dir != out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "sources.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if tmp_dir.exists() and not any(p.name != "sources.json" for p in tmp_dir.iterdir()):
            (tmp_dir / "sources.json").unlink(missing_ok=True)
            tmp_dir.rmdir()
    print(f"      building_id = {bid}")

    # ── [2] crawl ───────────────────────────────────────────────────────────
    man_path = out_dir / "manifest.json"
    if args.skip_crawl and man_path.exists():
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        print(f"[2/4] Dùng lại manifest.json ({manifest['ok']}/{manifest['total']} nguồn ok)")
    else:
        srcs = sorted(result["sources"], key=lambda s: s["priority"])
        manifest = crawl.run(bid, srcs, out_dir, fresh=args.fresh, timeout=args.timeout,
                             shots=not args.no_shots, headful=args.headful)

    # ── [3] trích ───────────────────────────────────────────────────────────
    ex_path = out_dir / "extract_text.json"
    if args.skip_extract and ex_path.exists():
        text_out = json.loads(ex_path.read_text(encoding="utf-8"))
        print(f"[3/4] Dùng lại extract_text.json")
    else:
        text_out = extract.run(out_dir, manifest, resolved)

    fp_path = out_dir / "extract_floorplan.json"
    if args.skip_vision:
        fp_out = json.loads(fp_path.read_text(encoding="utf-8")) if fp_path.exists() else {"plans": []}
        print("[3b] Bỏ qua vision (--skip-vision)")
    else:
        ut_hint = [{k: r.get(k) for k in ("type_code", "layout_class", "bedrooms",
                                          "area_gross_m2", "area_net_m2")}
                   for r in text_out["unit_type"]["records"]]
        fp_out = floorplan.run(out_dir, ut_hint, limit=args.max_floorplans)

    verified = floorplan.load_verified(out_dir, bid)

    # ── [4] lắp ráp, kiểm tra, ghi ─────────────────────────────────────────
    tables, prov, warns, bench = assemble.assemble(
        bid, resolved, text_out, fp_out, verified, manifest,
        linked_case_id=args.linked_case_id, is_target=args.target)
    warns += validate.check(tables, prov)
    csv_path = writer.run(bid, tables, prov, bench, warns, out_dir)
    print(f"      → {csv_path}")

    if tables["unit_room"]:
        p = floorplan.write_refer(out_dir, bid, [
            {**r, "type_code": next((u["type_code"] for u in tables["unit_type"]
                                     if u["unit_type_id"] == r["unit_type_id"]), ""),
             "source_file": next((x["source_file"] for x in fp_out.get("plans", [])), "")}
            for r in tables["unit_room"]])
        print(f"\n      → Xác nhận diện tích phòng (spec §4.2): điền cột `verified` = yes "
              f"trong\n        {p}\n        rồi chạy lại — dòng đã xác nhận sẽ thay kết quả vision.")


def main() -> None:
    ap = argparse.ArgumentParser(description="WS1 Building: tên toà nhà → feature CSV")
    ap.add_argument("building", nargs="?", help='Tên toà nhà, vd "Marina One Residences, Singapore"')
    ap.add_argument("--input", type=Path, help="File .txt, mỗi dòng 1 toà nhà")
    ap.add_argument("--building-id", default="", help="Ép building_id thay vì để agent tự sinh")
    ap.add_argument("--linked-case-id", default=None, help="FK sang case_benchmark của WS1 khu đô thị")
    ap.add_argument("--target", action="store_true", help="Đánh dấu is_target = true (sản phẩm GBAC)")
    ap.add_argument("--max-floorplans", type=int, default=20, help="Số ảnh mặt bằng đọc bằng vision")
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--fresh", action="store_true", help="Bỏ raw cũ, crawl lại từ đầu")
    ap.add_argument("--no-shots", action="store_true", help="Không chụp screenshot")
    ap.add_argument("--headful", action="store_true", help="Hiện trình duyệt khi crawl")
    ap.add_argument("--skip-discover", action="store_true", help="Dùng lại sources.json")
    ap.add_argument("--skip-crawl", action="store_true", help="Dùng lại manifest.json")
    ap.add_argument("--skip-extract", action="store_true", help="Dùng lại extract_text.json")
    ap.add_argument("--skip-vision", action="store_true", help="Bỏ bước đọc ảnh mặt bằng")
    ap.add_argument("--dry-run", action="store_true",
                    help="Chỉ in danh sách toà nhà đọc được từ input rồi dừng, không gọi API")
    ap.add_argument("--check-spec", action="store_true",
                    help="Đối chiếu feature_spec.md ↔ schema.py rồi dừng, không gọi API")
    ap.add_argument("--offline", action="store_true",
                    help="Giả lập model để test local (crawl vẫn thật, số liệu là RÁC)")
    ap.add_argument("--sources", type=Path,
                    help="File bảng nguồn tự cấp (mỗi dòng: URL [| purpose]) — bỏ qua bước tìm nguồn")
    args = ap.parse_args()

    if args.offline:
        config.OFFLINE = True

    if args.check_spec:
        problems = speccheck.check()
        if problems:
            print(f"\n✗ {len(problems)} chênh lệch:")
            for p in problems:
                print(f"  - {p}")
            sys.exit(1)
        print("\n✓ schema.py khớp feature_spec.md")
        return

    queries = []
    if args.input:
        queries = parse_buildings(args.input)
    elif args.building:
        queries = [args.building]
    if not queries:
        ap.error("cần tên toà nhà hoặc --input")

    if args.dry_run:
        print(f"Đọc {len(queries)} toà nhà từ {args.input or 'tham số dòng lệnh'}:")
        for i, q in enumerate(queries, 1):
            print(f"  {i:>2}. {q}")
        return

    if config.OFFLINE:
        print("⚠ CHẾ ĐỘ OFFLINE — model được giả lập, số liệu trong CSV là RÁC.\n"
              "  Dùng để kiểm tra đường ống, không dùng để phân tích.")
    else:
        config.load_api_key()
        where = config.BASE_URL or "api.anthropic.com"
        mode = " · chế độ tương thích (JSON qua prompt, không server tool)" if config.COMPAT else ""
        print(f"model = {config.MODEL} · effort = {config.EFFORT} · endpoint = {where}{mode}")
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{len(queries)} toà nhà")

    failed = []
    for q in queries:
        try:
            process(q, args)
        except anthropic.AuthenticationError:
            sys.exit("\n✗ ANTHROPIC_API_KEY không hợp lệ (401) — dừng toàn bộ.\n"
                     "  Đặt key hợp lệ rồi chạy lại:\n"
                     "    export ANTHROPIC_API_KEY=sk-ant-...\n"
                     f"  hoặc ghi vào {config.ROOT / '.env'}")
        except anthropic.RateLimitError as e:
            print(f"  ✗ {q}: chạm rate limit — {str(e)[:120]}")
            failed.append(q)
        except SystemExit as e:
            print(f"  ✗ {q}: {e}")
            failed.append(q)
        except Exception:
            traceback.print_exc()
            failed.append(q)
    if failed:
        print(f"\n✗ {len(failed)}/{len(queries)} toà nhà lỗi: {', '.join(failed)}")
        sys.exit(1)
    print(f"\n✓ Xong {len(queries)} toà nhà → {config.CSV_DIR}")


if __name__ == "__main__":
    main()
