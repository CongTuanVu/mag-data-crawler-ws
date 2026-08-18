"""Bộ quy tắc bóc tách CHUNG — chạy cho mọi site, không phụ thuộc DOM riêng.

Sinh lần đầu từ bản khảo sát `python run_extract.py survey`; sinh lại bằng
`python run_extract.py build` (agent đọc HTML rồi viết đè file này — bản cũ được
sao lưu ở code_extract/.bak/).

Nguyên tắc:
  · Tra theo NHÃN (所在地 / 総戸数 / 構造・階数 / 間取り / 専有面積 / 販売価格…),
    không theo vị trí DOM — nhãn 物件概要 gần như bất biến giữa các site Nhật.
  · Trang CĐT trước, cổng rao sau; trong một trang lấy Ô ĐẦU TIÊN khớp nhãn.
    Trang cổng còn liệt kê các toà LÂN CẬN ở phía dưới, lấy ô đầu là né được.
  · Mỗi trường có bộ kiểm tra riêng: giá trị không đúng dạng thì bỏ, không đoán.
  · Không dịch ở đây — runner lo qua code_extract/lexicon.py.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from code_extract import common as C

# ── Chọn trang & ô đáng tin ─────────────────────────────────────────────────
# Ô có giá trị lại chính là một nhãn khác → hàng tiêu đề bị ghép nhầm, bỏ.
KNOWN_LABELS = {
    "所在地", "住所", "交通", "交通機関", "最寄駅", "総戸数", "間取り", "専有面積",
    "販売価格", "価格", "築年月", "構造", "階建", "敷地面積", "建築面積", "売主",
    "施工会社", "設計", "竣工", "完成時期", "管理費", "修繕積立金", "タイプ",
    "マンション名", "偏差値", "推定相場", "バルコニー面積", "バルコニ-面積",
}


def ordered(pages: List[C.Page]) -> List[C.Page]:
    """Trang CĐT/chính thức trước, cổng rao sau — giữ nguyên thứ tự trong mỗi nhóm."""
    return sorted(pages, key=lambda p: (0 if C.official(p) else 1))


def first(pages: Iterable[C.Page], labels: Tuple[str, ...],
          ok: Optional[Callable[[str], bool]] = None) -> Optional[C.KV]:
    """Ô khớp nhãn ƯU TIÊN CAO NHẤT, trong đó lấy trang đáng tin nhất.

    Thứ tự nhãn quan trọng hơn thứ tự trang: `引き渡し可能年月` (bàn giao 2028) và
    `完成時期` (hoàn công 2027) là HAI mốc khác nhau, nên phải chọn theo đúng nhãn
    mình cần chứ không phải nhãn nào gặp trước.
    """
    pages = list(pages)
    for label in labels:
        for page in pages:
            for kv in page.kv:
                if label not in kv.label:
                    continue
                value = C.clean(kv.value)
                if not value or value in KNOWN_LABELS or value in ("-", "--", "×", "未定"):
                    continue
                if ok and not ok(value):
                    continue
                return kv
    return None


def scan(pages: Iterable[C.Page], pattern: str) -> Optional[Tuple[C.Page, re.Match]]:
    """Quét regex trên text phẳng của từng trang, theo thứ tự tin cậy."""
    for page in pages:
        m = page.find(pattern)
        if m:
            return page, m
    return None


# ── Bộ kiểm tra giá trị ─────────────────────────────────────────────────────
_JP_ADDR = re.compile(r"(都|道|府|県|区|市|町|村)")
_HAS_DIGIT = re.compile(r"\d")


def is_address(v: str) -> bool:
    return bool(_JP_ADDR.search(v)) and len(v) >= 6


def is_count(v: str, lo: int = 1, hi: int = 20000) -> bool:
    n = C.int_of(v)
    return n is not None and lo <= n <= hi


def is_area(v: str, lo: float = 1.0, hi: float = 2_000_000.0) -> bool:
    a = C.area_m2(v)
    return a is not None and lo <= a <= hi


def is_company(v: str) -> bool:
    return 2 <= len(v) <= 80 and not v.isdigit()


# Pháp nhân Nhật: tên + hậu tố công ty. Bắt được cả chuỗi dính liền không dấu phân
# cách (`住友不動産株式会社東京建物株式会社大和ハウス工業株式会社` trên major7).
_COMPANY = re.compile(r"[^\s、,/／()（）]{2,20}?(?:株式会社|有限会社|ホ-ルディングス|ホールディングス)")
# Nhãn 販売会社 hay mở đầu bằng `売主・販売提携(代理)/` — chức danh, không phải tên.
_ROLE_NOISE = re.compile(r"(売主|販売提携|代理|事業主|分譲会社|販売会社)[・/／:：]*")


def _has_company(v: str) -> bool:
    return bool(_COMPANY.search(C.clean(v)))


def _companies(value: str, limit: int = 3) -> Optional[str]:
    """`売主・販売提携(代理)/住友不動産株式会社…` → `住友不動産株式会社; 東京建物株式会社`."""
    s = _ROLE_NOISE.sub("", C.clean(value))
    s = re.sub(r"[(（][^)）]*[)）]", " ", s)          # bỏ số giấy phép, hội viên
    names, seen = [], set()
    for m in _COMPANY.finditer(s):
        name = m.group(0).strip("・/／ ")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return "; ".join(names[:limit]) or None


# ── B1 building ─────────────────────────────────────────────────────────────
def _district(address: str) -> Optional[str]:
    m = re.search(r"([^\s都道府県]{1,8}[区市郡])", C.clean(address))
    return m.group(1) if m else None


def _status(year_handover: Optional[int], snippet: str) -> Optional[str]:
    """Trạng thái suy từ chính câu công bố tiến độ, không đoán ngoài nguồn."""
    s = C.clean(snippet)
    if "予定" in s or "工事中" in s or "建築中" in s:
        return "dang_xay"
    if year_handover and year_handover <= 2026:
        return "da_ban_giao"
    return None


SIGNATURE_TERMS = ("免震構造", "制震構造", "耐震構造", "二重床", "二重天井",
                   "アウトフレーム工法", "スカイラウンジ", "屋上庭園", "スカイデッキ")


def building(pages: List[C.Page], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    pages = ordered(pages)
    out: Dict[str, Any] = {}
    prov: List[Dict[str, str]] = []

    def take(field: str, kv: Optional[C.KV], value: Any) -> None:
        if kv is None or value is None or value == "" or value == []:
            return
        out[field] = value
        prov.append(C.prov(field, kv.page, kv.snippet, C.confidence_of(kv.page)))

    addr = first(pages, ("所在地", "住所"), is_address)
    if addr:
        take("address", addr, C.clean(addr.value).split("地図")[0].strip())
        take("district", addr, _district(addr.value))

    units = first(pages, ("総戸数", "総区画数"), lambda v: is_count(v, 2))
    take("num_units_total", units, units.int_() if units else None)

    floors = first(pages, ("構造・階数", "構造および階数", "構造規模", "階建", "構造"),
                   lambda v: bool(re.search(r"\d+\s*階", v)))
    if floors:
        above, below = C.jp_floors(floors.value)
        take("num_floors_above", floors, above)
        take("num_basements", floors, below)

    land = first(pages, ("敷地面積",), is_area)
    take("land_area_m2", land, land.m2() if land else None)

    gfa = first(pages, ("延床面積", "延べ床面積", "建築延床面積"), is_area)
    take("gfa_m2", gfa, gfa.m2() if gfa else None)

    handover = first(pages, ("引き渡し", "引渡", "入居予定", "入居時期"),
                     lambda v: C.jp_year(v) is not None)
    done = first(pages, ("竣工", "完成時期", "築年月", "建築年月", "建物竣工"),
                 lambda v: C.jp_year(v) is not None)
    mark = handover or done                  # bàn giao mới là year_handover (§B1)
    if mark:
        take("year_handover", mark, C.jp_year(mark.value))
    if done:
        take("status", done, _status(C.jp_year(done.value), done.value))

    launch = first(pages, ("販売開始", "販売スケジュ"), lambda v: C.jp_year(v) is not None)
    take("year_launch", launch, C.jp_year(launch.value) if launch else None)

    dev = first(pages, ("売主", "事業主", "分譲会社", "販売会社"), _has_company)
    take("developer", dev, _companies(dev.value) if dev else None)

    arch = first(pages, ("設計会社", "設計者", "設計・監理", "設計"), is_company)
    take("architect_firm", arch, C.clean(arch.value).split("、")[0] if arch else None)

    kind = first(pages, ("種別", "物件種別"), lambda v: len(v) <= 20)
    if kind and "マンション" in kind.value:
        take("building_type", kind, "chung_cu")

    height = scan(pages, r"(?:最高)?高さ\s*(?:約)?\s*([\d.]+)\s*m")
    if height:
        page, m = height
        try:
            value = float(m.group(1))
        except ValueError:
            value = None
        if value and 3 <= value <= 700:
            out["height_m"] = value
            prov.append(C.prov("height_m", page, m.group(0), C.confidence_of(page)))

    ceiling = scan(pages, r"天井高\s*(?:約)?\s*([\d.]+)\s*(m|mm|ｍ)")
    if ceiling:
        page, m = ceiling
        try:
            value = float(m.group(1))
        except ValueError:
            value = None
        if value:
            value = value / 1000 if m.group(2) == "mm" else value
            if 2.0 <= value <= 6.0:
                out["floor_to_ceiling_m"] = round(value, 2)
                prov.append(C.prov("floor_to_ceiling_m", page, m.group(0), C.confidence_of(page)))

    # Hệ số đỗ xe: chỉ tính khi CẢ HAI con số đều được nguồn nêu rõ.
    park = first(pages, ("駐車場",), lambda v: bool(re.search(r"\d+\s*台", v)))
    if park and out.get("num_units_total"):
        slots = C.int_of(re.search(r"(\d+)\s*台", C.clean(park.value)).group(1))
        if slots:
            take("parking_ratio", park, round(slots / out["num_units_total"], 3))

    features, feature_kv = [], None
    for page in pages:
        for term in SIGNATURE_TERMS:
            if term in page.flat and term not in features:
                features.append(term)
                feature_kv = feature_kv or C.KV(term, term, C.squash(page.flat[:300]), page)
    take("signature_features", feature_kv, features or None)

    if not out:
        return []
    return [C.rec(out, prov)]


# ── B2 unit_type ────────────────────────────────────────────────────────────
TYPE_LABELS = ("タイプ", "プラン", "住戸タイプ", "間取りタイプ", "TYPE", "Type")
_TYPE_CODE = re.compile(r"^[A-Za-z]{0,3}[-‐]?\d{1,3}[A-Za-z]?$|^[A-Za-z]{1,3}[-‐]\d{1,3}[A-Za-z]?$")
_TITLE_CODE = re.compile(r"([A-Z]{1,3}[-‐]?\d{1,3}[A-Z]?)\s*タイプ")


def _type_code(raw: str) -> Optional[str]:
    """`F-120K Type` → `F-120K`; loại các giá trị rõ ràng không phải mã."""
    s = C.clean(raw).replace("Type", "").replace("タイプ", "").strip(" ：:")
    s = s.split()[0] if s.split() else ""
    if not s or len(s) > 16 or s in KNOWN_LABELS:
        return None
    if not re.search(r"[A-Za-z0-9]", s):
        return None
    return s


def _unit_from_block(block: Dict[str, C.KV], page: C.Page,
                     code: Optional[str]) -> Optional[Dict[str, Any]]:
    if not code:
        return None
    values: Dict[str, Any] = {"type_code": code}
    prov = [C.prov("type_code", page, block["__code__"].snippet, C.confidence_of(page))]

    madori_kv = block.get("間取り")
    if madori_kv:
        parsed = C.madori(madori_kv.value)
        for field, value in parsed.items():
            if value is not None:
                values[field] = value
                prov.append(C.prov(field, page, madori_kv.snippet, C.confidence_of(page)))

    area_kv = block.get("専有面積")
    if area_kv:
        lo, hi = C.area_range_m2(area_kv.value)
        basis = C.area_basis(area_kv.label + " " + area_kv.value)
        if basis == "khong_ro" and "専有" in area_kv.label:
            basis = "thong_thuy"                 # 専有面積 = nội thất, spec quy tắc 4
        if lo and 8 <= lo <= 1000:
            field = "area_net_m2" if basis == "thong_thuy" else "area_gross_m2"
            values[field] = lo
            values["area_basis_reported"] = basis
            prov.append(C.prov(field, page, area_kv.snippet, C.confidence_of(page)))
            prov.append(C.prov("area_basis_reported", page, area_kv.snippet,
                               C.confidence_of(page)))

    bal_kv = block.get("バルコニー面積")
    if bal_kv:
        value = C.area_m2(bal_kv.value)
        if value and 0.5 <= value <= 300:
            values["area_balcony_m2"] = value
            prov.append(C.prov("area_balcony_m2", page, bal_kv.snippet, C.confidence_of(page)))

    if page.purpose == "floorplan" and page.url:
        values["floorplan_url"] = page.url
        prov.append(C.prov("floorplan_url", page, page.url, C.confidence_of(page)))

    if len(values) <= 1:                          # chỉ có mã, không có dữ liệu nào
        return None
    return C.rec(values, prov)


def unit_type(pages: List[C.Page], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Đọc bảng loại căn: mỗi khối bắt đầu ở ô `タイプ`/`プラン` cho tới khối kế.

    Bảng loại căn ở cả site CĐT lẫn cổng đều là chuỗi ô nhãn→giá trị lặp lại
    (タイプ → 間取り → 専有面積 → バルコニー面積), nên duyệt tuần tự bắt được cả
    bảng dọc lẫn bảng ngang mà không cần biết DOM.
    """
    out: List[Dict[str, Any]] = []
    for page in ordered(pages):
        block: Dict[str, C.KV] = {}
        code: Optional[str] = None
        title_code = _TITLE_CODE.search(C.clean(page.title or ""))

        def flush() -> None:
            nonlocal block, code
            if block and code:
                record = _unit_from_block(block, page, code)
                if record:
                    out.append(record)
            block, code = {}, None

        for kv in page.kv:
            label = kv.label
            if any(lb in label for lb in TYPE_LABELS) and "間取り" not in label:
                flush()
                code = _type_code(kv.value)
                block = {"__code__": kv}
                continue
            if "間取り" in label:
                if code is None and title_code:   # trang riêng của một type
                    code = title_code.group(1)
                    block = {"__code__": kv}
                block.setdefault("間取り", kv)
            elif "専有面積" in label or "専有" in label and "面積" in label:
                block.setdefault("専有面積", kv)
            elif "バルコニ" in label and "面積" in label and "サ-ビス" not in label \
                    and "サービス" not in label:
                block.setdefault("バルコニー面積", kv)
        flush()

    for page in ordered(pages):
        for block in text_type_blocks(page):
            values: Dict[str, Any] = {"type_code": block["code"]}
            if block["madori"]:
                values.update({k: v for k, v in C.madori(block["madori"]).items()
                               if v is not None})
            area = C.area_m2(block["area"]) if block["area"] else None
            if area and 8 <= area <= 1000:
                values["area_net_m2"] = area          # 専有面積 = thông thuỷ (quy tắc 4)
                values["area_basis_reported"] = "thong_thuy"
            if len(values) > 1:
                out.append(C.auto_prov(values, page, block["snippet"], C.confidence_of(page)))

    if out:
        return out
    # Nhiều CĐT Nhật chỉ công bố DẢI (2LDK~3LDK, 58.13㎡~83.68㎡) chứ không có bảng
    # từng loại căn. Suy ngược ra từng type là bịa (spec quy tắc 1) → để trống và
    # nói rõ lý do; số liệu dải vẫn nằm ở B1 và ở bước vision đọc bản vẽ.
    rng = first(ordered(pages), ("専有面積",), lambda v: "~" in v or "〜" in v)
    if rng:
        return {"records": [], "notes": (
            "nguồn chỉ công bố dải diện tích/間取り, không có bảng từng loại căn "
            f"({C.squash(rng.value, 60)}) — B2 để trống, chờ vision đọc bản vẽ")}
    return out


# ── Khối loại căn ở dạng TEXT (suumo, homes…) ───────────────────────────────
# Không phải bảng nhãn→giá trị mà là các dòng liền nhau:
#     S-55E / 2LDK+N(納戸)+WIC / 専有面積: / 58.13m / 2 / 価格: / 1億5800万円
# Bắt được khối này là thêm cả B2 lẫn giá theo từng loại căn cho B7.
_CODE_LINE = re.compile(r"^[A-Z]{1,3}[-‐]\d{1,3}[A-Za-z]{0,4}$")
_MADORI_LINE = re.compile(r"\d\s*(?:LDK|DK|SLDK|K|R)", re.I)


def text_type_blocks(page: C.Page, span: int = 8) -> List[Dict[str, Any]]:
    """Các khối loại căn dạng text của một trang, mỗi khối là một dict thô."""
    lines = [C.clean(x) for x in (page.text or "").splitlines()]
    out: List[Dict[str, Any]] = []
    for i, line in enumerate(lines):
        if not _CODE_LINE.fullmatch(line):
            continue
        window = [x for x in lines[i + 1: i + 1 + span] if x]
        joined = " ".join(window)
        if not _MADORI_LINE.search(joined):
            continue                       # thiếu 間取り → gần như chắc không phải loại căn
        block: Dict[str, Any] = {"code": line, "madori": None, "area": None,
                                 "price": None, "snippet": C.squash(line + " " + joined, 200)}
        for j, item in enumerate(window):
            if block["madori"] is None and _MADORI_LINE.search(item) and "面積" not in item:
                block["madori"] = item
            if "専有面積" in item:
                # Giá trị có thể ở ngay sau dấu hai chấm hoặc ở dòng kế tiếp.
                tail = item.split(":")[-1].split("：")[-1].strip()
                block["area"] = tail or (window[j + 1] if j + 1 < len(window) else None)
            if "価格" in item and "帯" not in item:
                tail = item.split(":")[-1].split("：")[-1].strip()
                block["price"] = tail or (window[j + 1] if j + 1 < len(window) else None)
        out.append(block)
    return out


# ── B4 floor_plate ──────────────────────────────────────────────────────────
def floor_plate(pages: List[C.Page], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Mặt bằng tầng gần như không bao giờ ở dạng text trong nguồn Nhật.

    Suy số căn/sàn từ 総戸数 ÷ số tầng là điều spec cấm (quy tắc 1), nên bảng này
    để trống và chờ bước vision (`run.py --skip-extract`) hoặc nhập tay.
    """
    return {"records": [], "notes": "mặt bằng tầng không có ở dạng text — chờ vision/nhập tay"}


# ── B5 handover_item ────────────────────────────────────────────────────────
# term nguồn → (item_code ascii, item_category). Runner sẽ dịch item_name.
HANDOVER_TERMS: Dict[str, Tuple[str, str]] = {
    "食器洗い乾燥機": ("may_rua_bat", "bep"),
    "ディスポーザー": ("may_nghien_rac", "bep"),
    "生ゴミディスポーザー": ("may_nghien_rac", "bep"),
    "IHクッキングヒーター": ("bep_tu", "bep"),
    "ガスコンロ": ("bep_gas", "bep"),
    "レンジフード": ("may_hut_mui", "bep"),
    "浄水器": ("may_loc_nuoc", "bep"),
    "人造大理石": ("mat_bep_da_nhan_tao", "bep"),
    "御影石": ("mat_bep_da_granite", "bep"),
    "ミストサウナ": ("xong_hoi_uot", "thiet_bi_ve_sinh"),
    "浴室暖房乾燥機": ("suoi_say_phong_tam", "thiet_bi_ve_sinh"),
    "追い焚き": ("ham_nong_nuoc_tam", "thiet_bi_ve_sinh"),
    "温水洗浄便座": ("bon_cau_voi_rua", "thiet_bi_ve_sinh"),
    "タンクレストイレ": ("bon_cau_khong_ket", "thiet_bi_ve_sinh"),
    "洗面化粧台": ("ban_lavabo", "thiet_bi_ve_sinh"),
    "床暖房": ("san_suoi", "dieu_hoa_thong_gio"),
    "全館空調": ("dieu_hoa_trung_tam", "dieu_hoa_thong_gio"),
    "24時間換気システム": ("thong_gio_24h", "dieu_hoa_thong_gio"),
    "二重床": ("san_doi", "san"),
    "二重天井": ("tran_doi", "tuong_tran"),
    "ペアガラス": ("kinh_hop", "cua"),
    "複層ガラス": ("kinh_nhieu_lop", "cua"),
    "Low-Eガラス": ("kinh_low_e", "cua"),
    "オートロック": ("khoa_tu_dong_sanh", "an_ninh_pccc"),
    "ハンズフリーキー": ("khoa_hands_free", "an_ninh_pccc"),
    "防犯カメラ": ("camera_an_ninh", "an_ninh_pccc"),
    "TVモニター付インターホン": ("chuong_hinh", "an_ninh_pccc"),
    "24時間有人管理": ("quan_ly_24h", "an_ninh_pccc"),
    "宅配ボックス": ("tu_nhan_hang", "thiet_bi_dien"),
    "EV充電": ("tram_sac_xe_dien", "thiet_bi_dien"),
    "ウォークインクローゼット": ("tu_ao_di_vao", "cua"),
    "シューズインクローゼット": ("tu_giay_di_vao", "cua"),
    "エレベーター": ("thang_may", "thang_may"),
    # Mở rộng từ đối chiếu với bản LLM: chỉ nhận thuật ngữ DÙNG CHUNG giữa các dự
    # án. Tên riêng từng toà (お引越し無料パック, エスガード…) cố tình không đưa vào —
    # từ điển cố định không thể liệt kê hết, đó là phần vision/LLM lo.
    "システムキッチン": ("bep_lien_hoan", "bep"),
    "カウンターキッチン": ("bep_quay", "bep"),
    "天然石カウンター": ("mat_ban_da_tu_nhien", "bep"),
    "節水シャワー": ("bat_sen_tiet_kiem", "thiet_bi_ve_sinh"),
    "シャワー水栓": ("voi_sen", "thiet_bi_ve_sinh"),
    "手洗いカウンター": ("ban_rua_tay", "thiet_bi_ve_sinh"),
    "ユニットバス": ("phong_tam_lien_khoi", "thiet_bi_ve_sinh"),
    "フローリング": ("san_go", "san"),
    "高遮音床": ("san_cach_am", "san"),
    "対震ドア枠": ("khung_cua_chong_dong_dat", "cua"),
    "網戸": ("cua_luoi", "cua"),
    "玄関収納": ("tu_giay_sanh", "cua"),
    "火災感知器": ("dau_bao_chay", "an_ninh_pccc"),
    "スプリンクラー": ("sprinkler", "an_ninh_pccc"),
    "防災備品": ("vat_tu_phong_chong_thien_tai", "an_ninh_pccc"),
    "非常用発電機": ("may_phat_dien_du_phong", "thiet_bi_dien"),
    "太陽光発電": ("dien_mat_troi", "thiet_bi_dien"),
    "電気自動車充電設備": ("tram_sac_xe_dien", "thiet_bi_dien"),
    "ブロードバンド": ("ha_tang_internet", "thiet_bi_dien"),
    "インターネット": ("ha_tang_internet", "thiet_bi_dien"),
    "洗濯機置場": ("cho_dat_may_giat", "thiet_bi_dien"),
    "ZEH": ("chung_nhan_zeh", "thiet_bi_dien"),
    "スマートロック": ("khoa_thong_minh", "smart_home"),
    "スマートホーム": ("smart_home", "smart_home"),
    "制振": ("ket_cau_giam_chan", "an_ninh_pccc"),
}

OPTION_HINTS = ("オプション", "有償", "別途", "無償ではありません", "選択可能",
                "メニュープラン", "アップグレード")


def _near(text: str, term: str, span: int = 140) -> str:
    """Câu chứa từ khoá (không phải cửa sổ ±N ký tự — xem C.sentence_around)."""
    return C.sentence_around(text, term, span)


def handover_item(pages: List[C.Page], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Nhận diện hạng mục bàn giao theo từ khoá thiết bị trên trang CĐT.

    Chỉ đọc trang chính thức (spec: cổng rao mô tả thiết bị của toà khác trong
    phần gợi ý). `is_included` = false khi quanh từ khoá có chữ オプション/有償.
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for page in ordered(pages):
        if not C.official(page):
            continue
        flat = page.flat
        for term, (code, category) in HANDOVER_TERMS.items():
            if term not in flat or code in seen:
                continue
            seen.add(code)
            window = _near(flat, term)
            included = not any(hint in window for hint in OPTION_HINTS)
            out.append(C.auto_prov(
                {"item_code": code, "item_category": category, "item_name": term,
                 "item_spec": C.squash(window, 200), "is_included": included},
                page, window or term, C.confidence_of(page)))
    return out


# ── B6 amenity ──────────────────────────────────────────────────────────────
# term nguồn → (slug, amenity_category, is_indoor)
AMENITY_TERMS: Dict[str, Tuple[str, str, Optional[bool]]] = {
    "ラウンジ": ("lounge", "cong_dong_su_kien", True),
    "スカイラウンジ": ("sky_lounge", "cong_dong_su_kien", True),
    "ゲストルーム": ("guest_room", "cong_dong_su_kien", True),
    "パーティールーム": ("party_room", "cong_dong_su_kien", True),
    "フィットネス": ("fitness", "the_thao_gym", True),
    "ジム": ("gym", "the_thao_gym", True),
    "プール": ("pool", "be_boi", True),
    "キッズルーム": ("kids_room", "tre_em", True),
    "スタディルーム": ("study_room", "khong_gian_lam_viec", True),
    "ワークスペース": ("work_space", "khong_gian_lam_viec", True),
    "コワーキング": ("coworking", "khong_gian_lam_viec", True),
    "ライブラリー": ("library", "van_hoa_nghe_thuat", True),
    "シアタールーム": ("theater_room", "van_hoa_nghe_thuat", True),
    "コンシェルジュ": ("concierge", "dich_vu_le_tan", True),
    "トランクルーム": ("trunk_room", "do_xe", True),
    "駐輪場": ("bike_parking", "do_xe", None),
    "機械式駐車場": ("mechanical_parking", "do_xe", None),
    "駐車場": ("parking", "do_xe", None),
    "屋上庭園": ("rooftop_garden", "canh_quan_vuon", False),
    "中庭": ("courtyard", "canh_quan_vuon", False),
    "ドッグラン": ("dog_run", "thu_cung", False),
    "ペット足洗い場": ("pet_wash", "thu_cung", True),
    "スパ": ("spa", "suc_khoe_spa", True),
    "サウナ": ("sauna", "suc_khoe_spa", True),
    "エントランスホール": ("entrance_hall", "cong_dong_su_kien", True),
    "宅配ボックス": ("delivery_box", "dich_vu_le_tan", True),
    "フロントサービス": ("front_service", "dich_vu_le_tan", True),
    "コーチエントランス": ("coach_entrance", "dich_vu_le_tan", False),
    "クリーンステーション": ("waste_station", "dich_vu_le_tan", True),
    "ゴミ置場": ("waste_station", "dich_vu_le_tan", True),
    "防災倉庫": ("disaster_storage", "dich_vu_le_tan", True),
    "自転車置場": ("bike_parking", "do_xe", None),
    "バイク置場": ("motorbike_parking", "do_xe", None),
    "カーシェア": ("car_share", "do_xe", None),
    "保育所": ("nursery", "tre_em", True),
    "保育園": ("nursery", "tre_em", True),
    "デイサービス": ("day_care", "suc_khoe_spa", True),
    "会議室": ("meeting_room", "khong_gian_lam_viec", True),
    "キッチンスタジオ": ("kitchen_studio", "cong_dong_su_kien", True),
    "カラオケ": ("karaoke", "van_hoa_nghe_thuat", True),
    "音楽室": ("music_room", "van_hoa_nghe_thuat", True),
    "ゴルフレンジ": ("golf_range", "the_thao_gym", True),
    "ペット飼育可": ("pet_allowed", "thu_cung", None),
}

FLOOR_NEAR = re.compile(r"(地下\s*\d+\s*階|B\d|\d{1,2}\s*階|屋上|ル-フ|ルーフ)")


def amenity(pages: List[C.Page], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tiện ích nội khu nhận theo từ khoá, chỉ trên trang chính thức của toà."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for page in ordered(pages):
        if not C.official(page):
            continue
        flat = page.flat
        for term, (slug, category, indoor) in AMENITY_TERMS.items():
            if term not in flat or slug in seen:
                continue
            seen.add(slug)
            window = _near(flat, term, 120)
            values: Dict[str, Any] = {
                "slug": slug, "amenity_category": category, "amenity_name": term,
                "amenity_name_local": term, "is_indoor": indoor,
                "is_highlight": page.purpose == "amenities" or None,
            }
            floor = FLOOR_NEAR.search(window)
            if floor:
                values["floor_level"] = floor.group(1)
            out.append(C.auto_prov(values, page, window or term, C.confidence_of(page)))
    return out


# ── B7 price_obs ────────────────────────────────────────────────────────────
PRICE_LABELS = ("販売価格", "予定販売価格", "価格帯", "予定価格")
PERIOD_LABELS = ("情報更新日", "情報公開日", "販売スケジュ", "取引条件の有効期限")


def _period(pages: List[C.Page], page: C.Page, ctx: Dict[str, Any]) -> Optional[str]:
    kv = first([page], PERIOD_LABELS, lambda v: C.jp_period(v) is not None)
    if kv:
        return C.jp_period(kv.value)
    accessed = str(ctx.get("manifest", {}).get("accessed_at") or "")
    return accessed[:7] if re.match(r"\d{4}-\d{2}", accessed) else None


def price_obs(pages: List[C.Page], ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Giá mở bán công bố ở 販売価格. Nhật công bố theo CĂN → price_unit per_unit.

    Chỉ lấy ô đầu tiên của mỗi trang đáng tin; bảng đơn giá 坪単価/m²単価 trên
    cổng đánh giá là số của các toà lân cận nên không đụng tới.
    """
    out: List[Dict[str, Any]] = []
    have_range = False                                # giá dải toàn toà: chỉ lấy một lần
    for page in ordered(pages):
        if page.purpose in ("price_secondary", "market_report", "news_report"):
            continue
        period = _period(pages, page, ctx)
        for block in text_type_blocks(page):          # giá công bố theo từng loại căn
            value = C.money_jpy(block["price"]) if block["price"] else None
            if not value or value < 1e6 or not period:
                continue
            out.append(C.auto_prov(
                {"unit_type_code": block["code"], "market": "so_cap", "price_avg": value,
                 "currency": "JPY", "price_unit": "per_unit", "price_basis": "khong_ro",
                 "period": period,
                 "source_type": "cdt_official" if C.official(page) else "portal_niem_yet",
                 "listing_url": page.url or None},
                page, block["snippet"], C.confidence_of(page)))

        if have_range:
            continue                                  # giá theo loại căn vẫn quét tiếp ở trang sau
        kv = first([page], PRICE_LABELS, lambda v: C.money_jpy(v) is not None)
        if not kv:
            continue
        lo, hi = C.money_range_jpy(kv.value)
        if lo is None or lo < 1e6:                # dưới 1 triệu yên/căn là số rác
            continue
        if not period:
            continue
        values = {
            "market": "so_cap",
            "price_min": lo, "price_max": hi if hi and hi != lo else None,
            "currency": "JPY", "price_unit": "per_unit", "price_basis": "khong_ro",
            "period": period,
            "source_type": "cdt_official" if C.official(page) else "portal_niem_yet",
            "listing_url": page.url or None,
        }
        out.append(C.auto_prov(values, page, kv.snippet, C.confidence_of(page)))
        have_range = True
    return out
