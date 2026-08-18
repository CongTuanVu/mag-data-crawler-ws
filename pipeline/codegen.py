"""Sinh code bóc tách MỘT LẦN từ cấu trúc HTML đã crawl.

Thay vì mỗi toà gọi LLM đọc lại corpus (6 lượt × 209 toà), ta crawl xong hết,
cho agent xem cấu trúc HTML tiêu biểu ĐÚNG MỘT LƯỢT, rồi để nó viết
`code_extract/rules.py` + `code_extract/sites/*.py`. Từ đó việc bóc tách là chạy
code — không token, không chờ mạng.

Ba bước:
  survey()   gom mẫu HTML theo domain: nhãn→giá trị đã rút được + khung thẻ
  build()    1 lượt gọi model → cắt các khối `===== FILE: … =====` → ghi ra đĩa
  smoke()    import + chạy thử trên vài toà; lỗi thì gọi 1 lượt sửa kèm traceback
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from code_extract import common as C
from code_extract import runner

from . import config, llm, schema

CODE_DIR = config.ROOT / "code_extract"
BACKUP_DIR = CODE_DIR / ".bak"
GENERATED = ("rules.py",)                       # + sites/*.py
FILE_MARK = re.compile(r"^=====\s*FILE:\s*(.+?)\s*=====\s*$", re.M)

MAX_KV_PER_PAGE = int(os.environ.get("WS1_CODEGEN_KV", "70"))
MAX_TEXT_PER_PAGE = int(os.environ.get("WS1_CODEGEN_TEXT", "1200"))
MAX_PAGES_PER_HOST = int(os.environ.get("WS1_CODEGEN_PAGES", "3"))
MAX_ONEOFF_HOSTS = int(os.environ.get("WS1_CODEGEN_ONEOFF", "14"))
MAX_HOSTS = int(os.environ.get("WS1_CODEGEN_HOSTS", "22"))
MAX_DIGEST_CHARS = int(os.environ.get("WS1_CODEGEN_CHARS", "420000"))
CODEGEN_MAX_TOKENS = int(os.environ.get("WS1_CODEGEN_MAX_TOKENS", "48000"))


# ── [1] Khảo sát ────────────────────────────────────────────────────────────
def _skeleton(page: C.Page, limit: int = 60) -> str:
    """Khung thẻ + class của trang — để agent viết được selector cho từng cổng."""
    soup = page.soup
    for tag in soup(["script", "style", "noscript", "svg", "path"]):
        tag.decompose()
    lines: List[str] = []

    def walk(node, depth: int) -> None:
        if len(lines) >= limit or depth > 6:
            return
        for child in getattr(node, "children", []):
            if getattr(child, "name", None) is None:
                continue
            cls = ".".join((child.get("class") or [])[:3])
            ident = child.get("id") or ""
            mark = f"{child.name}{'#' + ident if ident else ''}{'.' + cls if cls else ''}"
            own = C.squash(child.get_text(" ", strip=True), 60)
            if child.name in ("table", "dl", "ul", "ol"):
                own = f"[{len(child.find_all(['tr', 'dt', 'li']))} mục] {own}"
            lines.append("  " * depth + f"<{mark}> {own}")
            if len(lines) >= limit:
                return
            walk(child, depth + 1)

    body = soup.body or soup
    walk(body, 0)
    return "\n".join(lines)


def _digest(page: C.Page) -> str:
    kv = page.kv[:MAX_KV_PER_PAGE]
    kv_block = "\n".join(f"  {k.label} │ {C.squash(k.value, 160)}" for k in kv) or "  (không có)"
    return (f"===== TRANG {page.name} | {page.url}\n"
            f"purpose: {page.purpose} | title: {C.squash(page.title, 90)}\n"
            f"--- cặp nhãn→giá trị C.kv_pairs() rút được ({len(page.kv)} cặp, hiện {len(kv)}):\n"
            f"{kv_block}\n"
            f"--- khung thẻ HTML:\n{_skeleton(page)}\n"
            f"--- text đầu trang:\n{C.squash(page.text, MAX_TEXT_PER_PAGE)}\n")


def survey(raw_dir: Optional[Path] = None) -> Tuple[str, Dict[str, int]]:
    """Đọc toàn bộ output_raw/, gom mẫu theo domain → một bản mô tả cấu trúc."""
    raw_dir = raw_dir or config.RAW_DIR
    by_host: Dict[str, List[C.Page]] = defaultdict(list)
    buildings = 0
    for man_path in sorted(raw_dir.glob("*/manifest.json")):
        try:
            manifest = json.loads(man_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        buildings += 1
        for page in C.load_pages(man_path.parent, manifest):
            if page.html or page.text:
                by_host[page.host].append(page)

    counts = {h: len(v) for h, v in by_host.items()}
    recurring = sorted((h for h, v in by_host.items() if len(v) > 1),
                       key=lambda h: -len(by_host[h]))[:MAX_HOSTS]
    # Site CĐT riêng chỉ xuất hiện một lần; lấy mẫu rải đều theo thứ tự tên để
    # bản khảo sát không toàn cùng một nhà phát triển.
    singles = sorted(h for h, v in by_host.items() if len(v) == 1)
    step = max(1, len(singles) // MAX_ONEOFF_HOSTS)
    oneoff = singles[::step][:MAX_ONEOFF_HOSTS]

    blocks: List[str] = []
    blocks.append(f"# KHẢO SÁT {buildings} toà · {sum(counts.values())} trang · "
                  f"{len(counts)} domain\n"
                  f"Domain lặp lại (nên có sites/*.py riêng): "
                  + ", ".join(f"{h}×{counts[h]}" for h in recurring[:20]) + "\n"
                  f"Domain một lần (site CĐT riêng từng toà — chỉ rules.py chung lo được): "
                  f"{len([h for h, v in by_host.items() if len(v) == 1])} domain\n")
    for host in recurring:
        pages = sorted(by_host[host], key=lambda p: -len(p.kv))[:MAX_PAGES_PER_HOST]
        blocks.append(f"\n########## DOMAIN LẶP LẠI: {host} ({counts[host]} trang)\n"
                      + "\n".join(_digest(p) for p in pages))
    if oneoff:
        blocks.append(f"\n########## MẪU SITE CĐT RIÊNG ({len(oneoff)} domain, mỗi domain 1 trang)")
        for host in oneoff:
            blocks.append("\n".join(_digest(p) for p in by_host[host][:1]))

    digest, kept = "", 0
    for block in blocks:                        # trần độ dài: cắt ở ranh giới domain
        if kept + len(block) > MAX_DIGEST_CHARS:
            digest += (f"\n\n[cắt bớt — bản khảo sát chạm trần {MAX_DIGEST_CHARS:,} ký tự, "
                       f"còn {len(blocks) - blocks.index(block)} khối chưa đưa vào]")
            break
        digest += block + "\n"
        kept += len(block)
    return digest, counts


# ── [2] Sinh code ───────────────────────────────────────────────────────────
def _schema_brief() -> str:
    """Danh sách trường từng bảng, đúng tên và kiểu — code sinh ra phải khớp."""
    out = []
    for name in schema.TEXT_TABLES:
        t = schema.TABLES[name]
        rows = "\n".join(f"    {f.name:26} {f.typ:22} {f.desc}" for f in t.llm)
        out.append(f"  {t.label} `{t.name}` — {t.unit}\n{rows}")
    enums = "\n".join(f"    {k}: {v}" for k, v in schema.V.items())
    return ("BẢNG & TRƯỜNG (tên phải khớp TUYỆT ĐỐI):\n" + "\n".join(out)
            + "\n\nDANH MỤC ENUM hợp lệ:\n" + enums)


SYSTEM = """\
Bạn là kỹ sư viết TRÌNH BÓC TÁCH cho workstream WS1 Building. Nhiệm vụ: đọc mô tả
cấu trúc HTML thật đã crawl, rồi VIẾT CODE PYTHON bóc tách dữ liệu — không phải
tự trích dữ liệu.

Code bạn viết sẽ chạy trên HÀNG TRĂM toà nhà mà bạn KHÔNG được xem trước, nên:

1. VIẾT THEO NHÃN, KHÔNG THEO VỊ TRÍ. Bảng 物件概要 của mỗi site khác nhau về
   DOM nhưng gần như luôn dùng chung bộ nhãn (所在地 / 総戸数 / 構造・階数 /
   竣工 / 間取り / 専有面積 / 販売価格 / 交通 …). Dùng `page.lookup("所在地", …)`
   thay vì `soup.select_one("table tr:nth-child(3) td")`.
2. KHÔNG BAO GIỜ NÉM LỖI. Nguồn thiếu nhãn, giá trị lạ, HTML vỡ → trả None hoặc
   bỏ record, không raise. Mọi truy cập chỉ số/khoá phải an toàn.
3. KHÔNG BỊA. Không suy diện tích phòng từ tổng diện tích, không chia đều
   num_units_total/num_floors, không đoán giá. Nguồn không nêu → None.
4. MỌI TRƯỜNG KHÁC None PHẢI CÓ ĐÚNG MỘT DÒNG PROVENANCE với `source_file` là
   `page.name` và `snippet` là câu/ô GỐC CHƯA QUY ĐỔI. Dùng `C.auto_prov(...)`
   khi cả record lấy từ một ô, `C.rec(...)` + `C.prov(...)` khi mỗi trường một ô.
5. QUY ĐỔI ĐƠN VỊ ngay khi trích, qua helper C (㎡/坪/帖, 億円/万円). Không tự
   quy đổi giữa tim tường ↔ thông thuỷ.
6. KHÔNG DỊCH trong code. Cứ trả nguyên văn tiếng Nhật cho các trường mô tả —
   runner sẽ đưa qua `code_extract/lexicon.py`. Riêng trường enum thì phải trả
   đúng mã trong danh mục.
7. Chỉ dùng thư viện chuẩn + `bs4` + `code_extract.common as C`. Không mạng,
   không đọc file ngoài, không `eval`/`exec`.

ĐỊNH DẠNG TRẢ LỜI — chỉ gồm các khối file, không lời dẫn, không ```:

===== FILE: rules.py =====
<toàn bộ nội dung file>
===== FILE: sites/suumo_jp.py =====
<toàn bộ nội dung file>

Đường dẫn hợp lệ: `rules.py` hoặc `sites/<tên>.py`. Không ghi đè common.py,
runner.py, lexicon.py.
"""


def _contract() -> str:
    common_src = (CODE_DIR / "common.py").read_text(encoding="utf-8")
    return f"""\
HỢP ĐỒNG CODE

`code_extract/rules.py` — bộ quy tắc CHUNG, chạy cho mọi site:

    from code_extract import common as C

    def building(pages, ctx) -> list[dict]      # trả 0..n record, runner gộp về 1
    def unit_type(pages, ctx) -> list[dict]
    def floor_plate(pages, ctx) -> list[dict]
    def handover_item(pages, ctx) -> list[dict]
    def amenity(pages, ctx) -> list[dict]
    def price_obs(pages, ctx) -> list[dict]

`code_extract/sites/<tên>.py` — override cho MỘT cổng, nhận từng trang một:

    HOSTS = ("suumo.jp",)                      # khớp cả subdomain
    def unit_type(page, ctx) -> list[dict]     # chỉ khai hàm nào cổng đó làm tốt hơn

Record của sites được ưu tiên khi trùng khoá khử trùng; rules chung lấp chỗ trống.
`ctx` có: building_id, resolved (tên/nước/thành phố), manifest, out_dir, pages.

Runner tự làm giúp bạn — ĐỪNG làm lại: ép kiểu & lọc enum sai, bù trường thiếu
bằng None, khử trùng record, gộp B1 về đúng 1 dòng, dịch JP→VI, ghi file.

TOÀN BỘ API `code_extract/common.py` (import sẵn là `C`) — chỉ dùng những gì có ở đây:
```python
{common_src}
```
"""


def build(raw_dir: Optional[Path] = None, *, extra_note: str = "") -> List[Path]:
    """Một lượt gọi model → ghi rules.py + sites/*.py. Trả danh sách file đã ghi."""
    print("[codegen] khảo sát HTML đã crawl…")
    digest, counts = survey(raw_dir)
    print(f"      {len(counts)} domain · bản khảo sát {len(digest):,} ký tự")

    system = [
        {"type": "text", "text": SYSTEM},
        {"type": "text", "text": "<feature_spec>\n" + config.read_spec() + "\n</feature_spec>"},
        {"type": "text", "text": _schema_brief()},
        llm.cached(_contract()),
    ]
    user = ("Dưới đây là cấu trúc HTML thật đã crawl. Viết trình bóc tách phủ được cả các "
            "site CĐT riêng (chỉ xuất hiện một lần) lẫn các cổng lặp lại.\n\n"
            + digest
            + "\n\nViết `rules.py` trước (đây là phần gánh phần lớn dữ liệu), rồi thêm "
              "`sites/*.py` cho những cổng có cấu trúc riêng đáng làm. Ưu tiên độ phủ và "
              "độ bền trước sự tinh vi."
            + (f"\n\nLƯU Ý THÊM:\n{extra_note}" if extra_note else ""))

    print("[codegen] gọi agent sinh code (1 lượt)…")
    text = llm.call_text(system=system, user_content=user,
                         max_tokens=CODEGEN_MAX_TOKENS, label="codegen")
    files = parse_files(text)
    if not files:
        raise SystemExit("[codegen] model không trả khối `===== FILE: … =====` nào")
    return write_files(files)


def parse_files(text: str) -> Dict[str, str]:
    """Cắt output thành {đường dẫn: mã nguồn}."""
    marks = list(FILE_MARK.finditer(text))
    out: Dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        path = m.group(1).strip().lstrip("./")
        code = text[m.end():end].strip("\n")
        code = re.sub(r"^```(?:python)?\n|\n```$", "", code)     # lỡ bọc fence
        if code.strip():
            out[path] = code + "\n"
    return out


def write_files(files: Dict[str, str]) -> List[Path]:
    """Ghi code sinh ra, sao lưu bản cũ. Chặn ghi ra ngoài code_extract/."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    written: List[Path] = []
    for rel, code in files.items():
        if rel != "rules.py" and not re.fullmatch(r"sites/[a-z0-9_]+\.py", rel):
            print(f"      ! bỏ qua đường dẫn không hợp lệ: {rel}")
            continue
        target = (CODE_DIR / rel).resolve()
        if CODE_DIR.resolve() not in target.parents:
            print(f"      ! bỏ qua đường dẫn thoát thư mục: {rel}")
            continue
        if target.exists():
            backup = BACKUP_DIR / stamp / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        written.append(target)
        print(f"      → {target.relative_to(config.ROOT)} ({len(code.splitlines())} dòng)")
    (CODE_DIR / "sites" / "__init__.py").touch()
    if BACKUP_DIR.exists():
        print(f"      (bản cũ lưu ở {BACKUP_DIR.relative_to(config.ROOT)}/{stamp})")
    return written


# ── [3] Chạy thử & sửa ──────────────────────────────────────────────────────
def smoke(limit: int = 3, raw_dir: Optional[Path] = None) -> Tuple[bool, str, Dict[str, int]]:
    """Import + chạy thử trên vài toà. Trả (đạt, báo cáo lỗi, số record mỗi bảng)."""
    raw_dir = raw_dir or config.RAW_DIR
    totals: Dict[str, int] = {t: 0 for t in schema.TEXT_TABLES}
    problems: List[str] = []
    try:
        rules = runner.load_rules(reload=True)
        sites = runner.load_sites(reload=True)
    except runner.ExtractorMissing:
        raise
    except Exception:
        return False, f"import code sinh ra thất bại:\n{traceback.format_exc(limit=6)}", totals

    tried = 0
    for man_path in sorted(raw_dir.glob("*/manifest.json")):
        if tried >= limit:
            break
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        tried += 1
        try:
            out = runner.run(man_path.parent, manifest,
                             {"building_name": man_path.parent.name, "building_name_local": None,
                              "city": "", "country": ""},
                             building_id=man_path.parent.name, rules=rules, sites=sites,
                             write=False, quiet=True)
        except Exception:
            problems.append(f"[{man_path.parent.name}] vỡ khi chạy:\n"
                            f"{traceback.format_exc(limit=6)}")
            continue
        for name, block in out.items():
            totals[name] += len(block["records"])
            note = block.get("notes") or ""
            if "lỗi" in note:
                problems.append(f"[{man_path.parent.name}] {name}: {note[:600]}")
    if not tried:
        return False, f"không có toà nào trong {raw_dir} để chạy thử", totals
    if sum(totals.values()) == 0:
        problems.append("chạy xong nhưng KHÔNG trích được record nào ở bất kỳ bảng nào")
    return (not problems), "\n\n".join(problems[:6]), totals


def build_and_verify(raw_dir: Optional[Path] = None, *, repairs: int = 1) -> Dict[str, int]:
    """build() → smoke(); lỗi thì gọi thêm tối đa `repairs` lượt sửa kèm traceback."""
    build(raw_dir)
    for attempt in range(repairs + 1):
        ok, report, totals = smoke(raw_dir=raw_dir)
        summary = " · ".join(f"{k}={v}" for k, v in totals.items())
        if ok:
            print(f"[codegen] ✓ chạy thử đạt — {summary}")
            return totals
        print(f"[codegen] ✗ chạy thử lỗi:\n{report[:1500]}")
        if attempt >= repairs:
            print("[codegen] hết lượt sửa — xem lại code_extract/rules.py bằng tay, "
                  "hoặc chạy lại `python run_extract.py build`")
            return totals
        print(f"[codegen] gọi lượt sửa {attempt + 1}/{repairs}…")
        current = {"rules.py": (CODE_DIR / "rules.py").read_text(encoding="utf-8")}
        for p in sorted((CODE_DIR / "sites").glob("*.py")):
            if p.name != "__init__.py":
                current[f"sites/{p.name}"] = p.read_text(encoding="utf-8")
        build(raw_dir, extra_note=(
            "Bản code trước KHÔNG chạy được. Sửa và trả lại TOÀN BỘ file (đầy đủ, "
            "không diff, không rút gọn).\n\nLỖI:\n" + report[:6000]
            + "\n\nCODE HIỆN TẠI:\n"
            + "\n".join(f"----- {k} -----\n{v}" for k, v in current.items())[:60000]))
    return totals
