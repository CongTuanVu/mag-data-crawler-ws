"""Lớp helper ỔN ĐỊNH cho code bóc tách do agent sinh ra.

Quy ước: file này VIẾT TAY, không bị agent ghi đè. Code sinh ra (`rules.py`,
`sites/*.py`) chỉ được `from .. import common as C` rồi gọi các hàm ở đây —
nhờ vậy khi sinh lại code, phần khó (parse bảng HTML, quy đổi đơn vị, đối chiếu
provenance) vẫn giữ nguyên hành vi đã kiểm chứng.

Ba nhóm:
  Page/kv_pairs   HTML → danh sách (nhãn, giá trị, câu gốc) — 物件概要 của mọi site
  num/area/money  chuỗi → số, đã quy đổi đơn vị theo feature_spec §10
  rec/prov        gom record + dòng provenance đúng dạng extract_text.json
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from bs4 import BeautifulSoup

# ── Chuẩn hoá ký tự ─────────────────────────────────────────────────────────
# Site Nhật trộn chữ số toàn rộng (１２３) với nửa rộng; quy về nửa rộng ngay từ
# đầu để mọi regex số phía dưới chỉ cần lo một dạng.
_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９．，－―ー～〜％　（）：／",
    "0123456789.,---~~% ():/",
)
_WS = re.compile(r"[ \t　]+")


def clean(text: Any) -> str:
    """Chuẩn hoá khoảng trắng + chữ số toàn rộng. None → ''."""
    if text is None:
        return ""
    s = str(text).translate(_FULLWIDTH)
    s = s.replace("\xa0", " ").replace("​", "")
    s = _WS.sub(" ", s)
    return s.strip()


def squash(text: Any, limit: int = 300) -> str:
    """Một dòng gọn để làm `snippet` provenance (spec §5: tối đa 300 ký tự)."""
    s = re.sub(r"\s*\n\s*", " ", clean(text))
    return s[:limit]


# ── Trang ───────────────────────────────────────────────────────────────────
@dataclass
class Page:
    """Một trang đã crawl. `name` là tên file .txt dùng trong provenance."""
    name: str                       # vd 02_brillia_tower.txt
    url: str
    purpose: str
    title: str
    html: str
    text: str

    @functools.cached_property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.html or "", "html.parser")

    @functools.cached_property
    def kv(self) -> List["KV"]:
        return kv_pairs(self)

    @functools.cached_property
    def host(self) -> str:
        m = re.match(r"https?://([^/]+)", self.url or "")
        return (m.group(1) if m else "").lower()

    @functools.cached_property
    def flat(self) -> str:
        """Text một dòng — cho regex quét cả trang không vướng xuống dòng."""
        return clean(re.sub(r"\s*\n\s*", " ", self.text or ""))

    def find(self, pattern: str, flags: int = 0) -> Optional[re.Match]:
        return re.search(pattern, self.flat, flags)

    def lookup(self, *labels: str) -> Optional["KV"]:
        """Ô đầu tiên có nhãn CHỨA một trong các chuỗi truyền vào."""
        for kv in self.kv:
            if any(lb in kv.label for lb in labels):
                return kv
        return None

    def lookup_all(self, *labels: str) -> List["KV"]:
        return [kv for kv in self.kv if any(lb in kv.label for lb in labels)]


@dataclass
class KV:
    """Một ô nhãn→giá trị trong bảng 物件概要 / dl / dòng `Nhãn：giá trị`."""
    label: str
    value: str
    snippet: str
    page: Page

    def num(self, **kw) -> Optional[float]:
        return num(self.value, **kw)

    def int_(self, **kw) -> Optional[int]:
        return int_of(self.value, **kw)

    def m2(self) -> Optional[float]:
        return area_m2(self.value)

    def __bool__(self) -> bool:
        return bool(self.value)


_SKIP_TAGS = ("script", "style", "noscript", "template")
_LABEL_LINE = re.compile(r"^\s*([^:：\t]{1,28})\s*[:：\t]\s*(\S.{0,300})$")


def kv_pairs(page: Page) -> List[KV]:
    """Rút mọi cặp nhãn→giá trị của một trang.

    Ba nguồn, gộp lại và khử trùng: `<table>` (th/td hoặc cột đầu làm nhãn),
    `<dl>` (dt/dd), và dòng text dạng `Nhãn：giá trị`. Bảng 物件概要 của site CĐT
    Nhật, suumo, homes… đều rơi vào một trong ba dạng này nên một hàm ăn hết.
    """
    out: List[KV] = []
    soup = page.soup
    for tag in soup(_SKIP_TAGS):
        tag.decompose()

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False) or row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            raw = [clean(c.get_text(" ", strip=True)) for c in cells]
            # Bảng 2 cột: (nhãn, giá trị). Bảng 4 cột: (nhãn, giá trị, nhãn, giá trị).
            for i in range(0, len(raw) - 1, 2):
                label, value = raw[i], raw[i + 1]
                if label and value and len(label) <= 30:
                    out.append(KV(label, value, squash(" ".join(raw)), page))

    for dl in soup.find_all("dl"):
        dts, dds = dl.find_all("dt"), dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            label = clean(dt.get_text(" ", strip=True))
            value = clean(dd.get_text(" ", strip=True))
            if label and value and len(label) <= 30:
                out.append(KV(label, value, squash(f"{label} {value}"), page))

    for line in (page.text or "").splitlines():
        m = _LABEL_LINE.match(clean(line))
        if m:
            label, value = m.group(1).strip(), m.group(2).strip()
            if label and value:
                out.append(KV(label, value, squash(line), page))

    seen, uniq = set(), []
    for kv in out:
        key = (kv.label, kv.value)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(kv)
    return uniq


def load_pages(out_dir: Path, manifest: Dict[str, Any]) -> List[Page]:
    """Nạp mọi trang crawl thành công của một toà."""
    pages: List[Page] = []
    for s in manifest.get("sources", []):
        if s.get("status") != "ok" or not s.get("text_file"):
            continue
        txt_path = out_dir / s["text_file"]
        html_path = out_dir / s["raw_file"] if s.get("raw_file") else None
        text = txt_path.read_text(encoding="utf-8", errors="ignore") if txt_path.exists() else ""
        html = ""
        if html_path and html_path.exists() and html_path.suffix == ".html":
            html = html_path.read_text(encoding="utf-8", errors="ignore")
        pages.append(Page(name=Path(s["text_file"]).name, url=s.get("url", ""),
                          purpose=s.get("purpose", ""), title=s.get("page_title", ""),
                          html=html, text=text))
    return pages


# ── Số & đơn vị (feature_spec §10) ──────────────────────────────────────────
_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

AREA_UNITS = {                      # → m²
    "m2": 1.0, "㎡": 1.0, "m²": 1.0, "平米": 1.0, "平方メートル": 1.0, "sqm": 1.0,
    "坪": 3.30579, "평": 3.30579,
    "帖": 1.62, "畳": 1.62, "j": 1.62,
    "sqft": 0.092903, "sq ft": 0.092903, "ft2": 0.092903, "ft²": 0.092903,
}
MONEY_JPY = {"億円": 1e8, "億": 1e8, "万円": 1e4, "万": 1e4, "円": 1.0}


def num(text: Any, *, index: int = 0) -> Optional[float]:
    """Số thứ `index` trong chuỗi (đã bỏ dấu phẩy). Không có → None."""
    hits = _NUM.findall(clean(text))
    if index >= len(hits):
        return None
    try:
        return float(hits[index].replace(",", ""))
    except ValueError:
        return None


def nums(text: Any) -> List[float]:
    out = []
    for h in _NUM.findall(clean(text)):
        try:
            out.append(float(h.replace(",", "")))
        except ValueError:
            pass
    return out


def int_of(text: Any, *, index: int = 0) -> Optional[int]:
    v = num(text, index=index)
    return int(round(v)) if v is not None else None


def area_m2(text: Any) -> Optional[float]:
    """Diện tích → m², tự nhận đơn vị (㎡/坪/帖/sq ft). `70.27㎡` → 70.27."""
    s = clean(text)
    if not s:
        return None
    for unit, factor in sorted(AREA_UNITS.items(), key=lambda x: -len(x[0])):
        idx = s.lower().find(unit.lower())
        if idx == -1:
            continue
        head = s[:idx]
        hits = _NUM.findall(head)
        if not hits:
            continue
        try:
            return round(float(hits[-1].replace(",", "")) * factor, 2)
        except ValueError:
            continue
    return num(s)                    # không ghi đơn vị → coi như m²


def area_range_m2(text: Any) -> Tuple[Optional[float], Optional[float]]:
    """`70.27㎡ ~ 120.97㎡` → (70.27, 120.97). Một giá trị → (v, v)."""
    parts = [p for p in re.split(r"[~〜～]|\bto\b|—|–", clean(text)) if p.strip()]
    vals = [v for v in (area_m2(p) for p in parts) if v is not None]
    if not vals:
        return None, None
    return min(vals), max(vals)


def money_jpy(text: Any) -> Optional[float]:
    """`2億3,800万円` → 238000000.0 · `6980万円` → 69800000.0. Yên, không phải triệu yên."""
    s = clean(text)
    if not s:
        return None
    total, matched = 0.0, False
    for unit in ("億", "万"):
        m = re.search(rf"([\d,]+(?:\.\d+)?)\s*{unit}", s)
        if m:
            total += float(m.group(1).replace(",", "")) * MONEY_JPY[unit]
            matched = True
    if matched:
        return round(total, 2)
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*円", s)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def money_range_jpy(text: Any) -> Tuple[Optional[float], Optional[float]]:
    """`6,980万円 ~ 2億3,800万円` → (69800000.0, 238000000.0)."""
    parts = [p for p in re.split(r"[~〜～]|\bto\b|—|–", clean(text)) if p.strip()]
    vals = [v for v in (money_jpy(p) for p in parts) if v is not None]
    if not vals:
        return None, None
    return min(vals), max(vals)


# ── Nhật Bản: tầng, năm, 間取り (feature_spec quy tắc 4b) ────────────────────
def jp_floors(text: Any) -> Tuple[Optional[int], Optional[int]]:
    """`地上27階 地下1階建` → (27, 1). Không nêu hầm → (27, None)."""
    s = clean(text)
    above = below = None
    m = re.search(r"地上\s*(\d+)\s*階", s)
    if m:
        above = int(m.group(1))
    else:
        m = re.search(r"(\d+)\s*階建", s)
        if m:
            above = int(m.group(1))
    m = re.search(r"地下\s*(\d+)\s*階", s)
    if m:
        below = int(m.group(1))
    return above, below


def jp_year(text: Any) -> Optional[int]:
    """`2026年3月竣工` → 2026. Nhận cả `2026/03`, `令和8年` bỏ qua (trả None)."""
    s = clean(text)
    m = re.search(r"(19|20)\d{2}", s)
    return int(m.group(0)) if m else None


def jp_period(text: Any) -> Optional[str]:
    """`2026年3月` → `2026-03` · chỉ có năm → `2026-01`. Dùng cho B7.period."""
    s = clean(text)
    m = re.search(r"((?:19|20)\d{2})\s*[年/\-.]\s*(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r"(19|20)\d{2}", s)
    return f"{m.group(0)}-01" if m else None


_MADORI = re.compile(r"(\d+)\s*(LDK|DK|K|R)\s*(\+?\s*S|\+?\s*N|\+?\s*WIC|\+?\s*SIC)?", re.I)


def madori(text: Any) -> Dict[str, Any]:
    """`3LDK+S` → bedrooms 3, layout_class `3pn_plus`, has_multipurpose_room True.

    Quy tắc 4b: LDK KHÔNG tính là phòng ngủ; `1R`/`1K` → studio; hậu tố `+S`
    (サービスルーム) → biến thể `_plus`.
    """
    s = clean(text).upper().replace("ＬＤＫ", "LDK")
    m = _MADORI.search(s)
    if not m:
        return {"bedrooms": None, "layout_class": None, "has_multipurpose_room": None}
    n, kind, extra = int(m.group(1)), m.group(2).upper(), (m.group(3) or "").strip()
    plus = bool(extra) or "+S" in s.replace(" ", "") or "サービスルーム" in clean(text)
    if kind in ("R", "K") and n == 1:
        return {"bedrooms": 0, "layout_class": "studio", "has_multipurpose_room": plus or None}
    base = {1: "1pn", 2: "2pn", 3: "3pn"}.get(n, "4pn_plus")
    # Danh mục §8.5 chỉ có biến thể `_plus` cho 1pn/2pn; 3LDK+S vẫn là `3pn`,
    # phần `+S` được ghi lại ở has_multipurpose_room chứ không bịa mã mới.
    if plus and base in ("1pn", "2pn"):
        base += "_plus"
    return {"bedrooms": n, "layout_class": base, "has_multipurpose_room": plus or None}


# Cơ sở diện tích (spec quy tắc 4) — nhãn nguồn → enum area_basis.
AREA_BASIS_LABELS = {
    "壁芯": "tim_tuong", "壁心": "tim_tuong", "建築面積": "tim_tuong", "공급면적": "tim_tuong",
    "建筑面积": "tim_tuong", "供給面積": "tim_tuong",
    "専有面積": "thong_thuy", "内法": "thong_thuy", "전용면적": "thong_thuy",
    "套内面积": "thong_thuy",
}


def area_basis(text: Any) -> str:
    s = clean(text)
    for token, enum in AREA_BASIS_LABELS.items():
        if token in s:
            return enum
    return "khong_ro"


# ── Record & provenance ─────────────────────────────────────────────────────
def prov(field_name: str, page: Page, snippet: Any, confidence: str = "medium") -> Dict[str, str]:
    return {"field": field_name, "source_file": page.name,
            "snippet": squash(snippet), "confidence": confidence}


def rec(values: Dict[str, Any], provenance: Sequence[Dict[str, str]] = ()) -> Dict[str, Any]:
    """Record + provenance, bỏ sẵn các trường None cho gọn (runner sẽ bù lại)."""
    body = {k: v for k, v in values.items() if v is not None and v != ""}
    body["provenance"] = list(provenance)
    return body


def auto_prov(values: Dict[str, Any], page: Page, snippet: Any,
              confidence: str = "medium") -> Dict[str, Any]:
    """Record mà MỌI trường khác null dùng chung một câu gốc — tiện cho ô bảng."""
    body = {k: v for k, v in values.items() if v is not None and v != ""}
    lines = [prov(k, page, snippet, confidence) for k in body]
    body["provenance"] = lines
    return body


def slug(text: Any, fallback: str = "x") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", clean(text).lower()).strip("_")
    return s[:60] or fallback


def official(page: Page) -> bool:
    """Trang CĐT/chính thức → provenance confidence `high` (spec quy tắc 6)."""
    return page.purpose in ("official_overview", "brochure_pdf", "handover_spec",
                            "floorplan", "product_mix", "amenities")


def confidence_of(page: Page) -> str:
    if official(page):
        return "high"
    return "low" if page.purpose in ("price_secondary", "news_report") else "medium"


_SENT_SPLIT = re.compile(r"[。｡\n]|※|・{2,}")


def sentence_around(text: str, term: str, limit: int = 140) -> str:
    """Câu chứa `term`, cắt ở dấu 。/※/xuống dòng — tránh lấy nhầm câu bên cạnh.

    Cửa sổ ±N ký tự quanh từ khoá hay vắt sang đoạn không liên quan, làm `item_spec`
    thành rác; cắt theo ranh giới câu cho ra quy cách đọc được.
    """
    flat = clean(text)
    idx = flat.find(term)
    if idx == -1:
        return ""
    left = max((flat.rfind(mark, 0, idx) for mark in ("。", "｡", "※")), default=-1)
    m = _SENT_SPLIT.search(flat, idx + len(term))
    right = m.start() if m else len(flat)
    return flat[left + 1: right].strip()[:limit]
