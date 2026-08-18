#!/usr/bin/env python3
"""WS1 Building — bóc tách bằng CODE (không gọi LLM từng trang).

Luồng nhanh, chạy sau khi đã crawl xong toàn bộ danh sách:

    python run.py --input buildings.txt --crawl-only     # [1][2] crawl hết về output_raw/
    python run_extract.py build                          # 1 lượt agent → code_extract/rules.py
    python run_extract.py run --input buildings.txt      # [3][4] bằng code → output_csv/
    python run_extract.py translate                      # dịch gộp thuật ngữ còn sót
    python run.py --input buildings.txt --skip-discover --skip-crawl --skip-extract
                                                         # bổ sung B3 bằng vision khi cần

Lệnh phụ:
    survey                  in bản khảo sát cấu trúc HTML (thứ agent sẽ đọc)
    check <building_id>     chạy thử code trên một toà, in kết quả, không ghi CSV
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from code_extract import lexicon, runner
from pipeline import (assemble, codegen, config, floorplan, llm, merge, translate,
                      validate, writer)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


# ── Chọn toà để xử lý ───────────────────────────────────────────────────────
def crawled_dirs(raw_dir: Optional[Path] = None) -> List[Path]:
    """Thư mục toà đã crawl xong (có manifest.json)."""
    raw_dir = raw_dir or config.RAW_DIR
    return sorted(p.parent for p in raw_dir.glob("*/manifest.json"))


def select(args) -> List[Path]:
    dirs = crawled_dirs()
    if args.only:
        wanted = {config.slugify(o, "building") for o in args.only}
        dirs = [d for d in dirs if d.name in wanted]
        missing = wanted - {d.name for d in dirs}
        if missing:
            print(f"⚠ chưa crawl: {', '.join(sorted(missing))}")
    if getattr(args, "skip_done", False):
        dirs = [d for d in dirs if not (config.CSV_DIR / f"{d.name}.csv").exists()]
    if getattr(args, "limit", 0):
        dirs = dirs[:args.limit]
    return dirs


# ── Bóc tách + ghi CSV cho một toà (chạy trong tiến trình con) ──────────────
def process_one(out_dir: Path, opts: Dict[str, Any]) -> Tuple[str, Optional[str], Dict[str, int]]:
    """Bước [3] bằng code + bước [4] như cũ. Trả (tên toà, lỗi hoặc None, đếm record)."""
    bid = out_dir.name
    try:
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        sources_path = out_dir / "sources.json"
        resolved = {}
        if sources_path.exists():
            resolved = json.loads(sources_path.read_text(encoding="utf-8")).get("resolved", {})
        resolved.setdefault("building_name", bid.replace("_", " ").title())
        resolved.setdefault("building_name_local", None)
        resolved.setdefault("city", "")
        resolved.setdefault("country", "")

        text_out = runner.run(out_dir, manifest, resolved, building_id=bid, quiet=True)
        counts = {k: len(v["records"]) for k, v in text_out.items()}

        # Vision (B3) là bước LLM riêng, mặc định không chạy ở đây; nếu lần chạy
        # trước đã sinh extract_floorplan.json thì dùng lại để CSV không mất B3.
        fp_path = out_dir / "extract_floorplan.json"
        fp_out = (json.loads(fp_path.read_text(encoding="utf-8"))
                  if fp_path.exists() else {"plans": []})
        verified = floorplan.load_verified(out_dir, bid)

        tables, prov, warns, bench = assemble.assemble(
            bid, resolved, text_out, fp_out, verified, manifest,
            linked_case_id=opts.get("linked_case_id"), is_target=opts.get("is_target", False))
        warns += validate.check(tables, prov)
        writer.run(bid, tables, prov, bench, warns, out_dir)
        return bid, None, counts
    except SystemExit as e:
        # assemble/writer báo lỗi dữ liệu bằng SystemExit (vd corpus rỗng, thiếu B1).
        # Đó là lỗi của RIÊNG toà này — bắt lại để không giết cả mẻ 209 toà.
        return bid, str(e) or "SystemExit", {}
    except Exception:
        return bid, traceback.format_exc(limit=5), {}


# ── Lệnh ────────────────────────────────────────────────────────────────────
def cmd_build(args) -> int:
    if args.no_verify:
        codegen.build()
        print("[codegen] bỏ qua chạy thử (--no-verify)")
        return 0
    totals = codegen.build_and_verify(repairs=args.repairs)
    report = llm.usage_summary()
    if report:
        print(f"\n── Token đã dùng ──\n{report}")
    return 0 if sum(totals.values()) else 1


def cmd_survey(args) -> int:
    digest, counts = codegen.survey()
    if args.out:
        Path(args.out).write_text(digest, encoding="utf-8")
        print(f"→ {args.out} ({len(digest):,} ký tự, {len(counts)} domain)")
    else:
        print(digest)
    return 0


def cmd_check(args) -> int:
    out_dir = config.RAW_DIR / config.slugify(args.building_id, "building")
    if not (out_dir / "manifest.json").exists():
        sys.exit(f"chưa crawl: {out_dir}")
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    sources = out_dir / "sources.json"
    resolved = (json.loads(sources.read_text(encoding="utf-8")).get("resolved", {})
                if sources.exists() else {})
    for k, v in (("building_name", out_dir.name), ("building_name_local", None),
                 ("city", ""), ("country", "")):
        resolved.setdefault(k, v)
    out = runner.run(out_dir, manifest, resolved, building_id=out_dir.name, write=args.write)
    for name, block in out.items():
        if args.table and name != args.table:
            continue
        print(f"\n───── {name}: {len(block['records'])} record")
        if block.get("notes"):
            print(f"  ⚠ {block['notes'][:800]}")
        for r in block["records"][:args.rows]:
            filled = {k: v for k, v in r.items() if v is not None and k != "provenance"}
            print("  " + json.dumps(filled, ensure_ascii=False)[:900])
    misses = lexicon.misses()
    if misses:
        print(f"\n{len(misses)} thuật ngữ chưa có trong từ điển (đã ghi vào "
              f"{lexicon.MISS_PATH.name}) — chạy `python run_extract.py translate`")
    return 0


def cmd_run(args) -> int:
    runner.load_rules()                     # báo lỗi sớm nếu chưa sinh code
    dirs = select(args)
    if not dirs:
        print("Không có toà nào cần xử lý.")
        return 0
    opts = {"linked_case_id": args.linked_case_id, "is_target": args.target}
    print(f"{len(dirs)} toà · {args.workers} tiến trình song song · không gọi LLM")
    started = time.time()
    failed: List[str] = []
    totals: Dict[str, int] = {}

    def report(result) -> None:
        bid, error, counts = result
        if error:
            failed.append(bid)
            print(f"  ✗ {bid}: {error.strip().splitlines()[-1]}")
            if args.verbose:
                print(error)
            return
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        print(f"  ✓ {bid}: " + " ".join(f"{k}={v}" for k, v in counts.items()))

    if args.workers <= 1:
        for d in dirs:
            report(process_one(d, opts))
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(process_one, d, opts) for d in dirs]
            for future in as_completed(futures):
                report(future.result())

    took = time.time() - started
    ok = len(dirs) - len(failed)
    print(f"\n✓ {ok}/{len(dirs)} toà → {config.CSV_DIR} · {took:.1f}s "
          f"({took / max(1, len(dirs)):.2f}s/toà)")
    if totals:
        print("  tổng record: " + " · ".join(f"{k}={v}" for k, v in totals.items()))
    if args.merge:
        print("\n── Gộp CSV theo thread ──")
        merge.run(threads=args.workers, drop_evidence=args.no_evidence)

    pending = translate.pending()
    if pending:
        print(f"  {len(pending)} thuật ngữ chưa dịch — `python run_extract.py translate`")
    if failed:
        print(f"✗ {len(failed)} toà lỗi: {', '.join(failed)}")
        return 1
    return 0


def cmd_merge(args) -> int:
    merge.run(threads=args.threads, day=args.day,
              drop_evidence=args.no_evidence, clean=args.clean)
    return 0


def cmd_translate(args) -> int:
    translate.run(limit=args.limit, dry_run=args.dry_run)
    report = llm.usage_summary()
    if report:
        print(f"\n── Token đã dùng ──\n{report}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="1 lượt agent đọc HTML → sinh code_extract/rules.py")
    b.add_argument("--repairs", type=int, default=1,
                   help="Số lượt gọi lại để sửa nếu code sinh ra chạy lỗi (mặc định 1)")
    b.add_argument("--no-verify", action="store_true", help="Không chạy thử sau khi sinh")
    b.set_defaults(fn=cmd_build)

    s = sub.add_parser("survey", help="In bản khảo sát cấu trúc HTML (đầu vào của agent)")
    s.add_argument("--out", help="Ghi ra file thay vì in ra màn hình")
    s.set_defaults(fn=cmd_survey)

    c = sub.add_parser("check", help="Chạy thử code trên MỘT toà, không ghi CSV")
    c.add_argument("building_id")
    c.add_argument("--table", help="Chỉ in một bảng, vd unit_type")
    c.add_argument("--rows", type=int, default=5, help="Số record in ra mỗi bảng")
    c.add_argument("--write", action="store_true", help="Ghi cả extract_text.json")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("run", help="Bóc tách + ghi CSV cho mọi toà đã crawl")
    r.add_argument("--input", type=Path, help="Chỉ để đối chiếu danh sách (không bắt buộc)")
    r.add_argument("--only", nargs="*", help="Chỉ chạy vài toà (tên hoặc building_id)")
    r.add_argument("--skip-done", action="store_true", help="Bỏ toà đã có output_csv/<id>.csv")
    r.add_argument("--limit", type=int, default=0, help="Giới hạn số toà (thử nghiệm)")
    r.add_argument("--workers", type=int, default=8, help="Số tiến trình song song (mặc định 8)")
    r.add_argument("--linked-case-id", default=None)
    r.add_argument("--target", action="store_true")
    r.add_argument("--verbose", action="store_true", help="In đầy đủ traceback khi lỗi")
    r.add_argument("--no-merge", dest="merge", action="store_false",
                   help="Không gộp thành thread<N>_<ngày>.csv sau khi chạy")
    r.add_argument("--no-evidence", action="store_true",
                   help="Bỏ cột evidence_json khỏi file gộp (~85% dung lượng)")
    r.set_defaults(fn=cmd_run, merge=True)

    m = sub.add_parser("merge", help="Gộp CSV từng toà → thread<N>_<ngày>.csv "
                                     "(đọc file đã có, KHÔNG chạy lại bước trích)")
    m.add_argument("--threads", type=int, default=8, help="Số file thread (mặc định 8)")
    m.add_argument("--day", default="", help="Ngày trong tên file, YYYYMMDD (mặc định hôm nay)")
    m.add_argument("--no-evidence", action="store_true",
                   help="Bỏ cột evidence_json (~85% dung lượng)")
    m.add_argument("--keep-old", dest="clean", action="store_false",
                   help="Giữ file thread của lần gộp trước")
    m.set_defaults(fn=cmd_merge, clean=True)

    t = sub.add_parser("translate", help="Dịch gộp thuật ngữ còn sót → lexicon_auto.json")
    t.add_argument("--limit", type=int, default=0, help="Chỉ dịch N term nhiều lượt nhất")
    t.add_argument("--dry-run", action="store_true", help="Chỉ liệt kê, không gọi model")
    t.set_defaults(fn=cmd_translate)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
