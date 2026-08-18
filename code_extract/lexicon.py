"""Từ điển thuật ngữ nguồn → tiếng Việt (feature_spec quy tắc 8a/8b).

Code tĩnh không tự dịch được, nên mọi trường mô tả đi qua `vi()`:
  · trúng từ điển  → trả bản tiếng Việt
  · trượt          → giữ nguyên văn VÀ ghi lại vào `.lexicon_misses.json`

Sau khi chạy hết danh sách toà, `python run_extract.py translate` gom toàn bộ
term trượt thành 1–2 lượt LLM dịch gộp rồi ghi vào `lexicon_auto.json`; lần chạy
sau tra được ngay, không gọi model nữa.

SEED viết tay + mở rộng bởi agent; AUTO do bước translate sinh. Tra SEED trước.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Dict, Optional

DIR = Path(__file__).resolve().parent
AUTO_PATH = DIR / "lexicon_auto.json"
MISS_PATH = DIR / ".lexicon_misses.json"

CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")

# ── Từ điển hạt giống ───────────────────────────────────────────────────────
# Chỉ những thuật ngữ lặp lại ở gần như mọi toà Nhật; phần đuôi dài để bước
# translate lo. Khoá đã chuẩn hoá: bỏ khoảng trắng hai đầu, giữ nguyên chữ gốc.
SEED: Dict[str, str] = {
    # kết cấu & khái quát
    "鉄筋コンクリート造": "Kết cấu bê tông cốt thép",
    "鉄骨鉄筋コンクリート造": "Kết cấu bê tông cốt thép có lõi thép",
    "鉄骨造": "Kết cấu thép",
    "免震構造": "Kết cấu cách chấn",
    "制震構造": "Kết cấu giảm chấn",
    "耐震構造": "Kết cấu kháng chấn",
    "二重床": "Sàn đôi",
    "二重天井": "Trần đôi",
    "アウトフレーム工法": "Kết cấu khung đưa ra ngoài",
    "ペアガラス": "Kính hộp hai lớp",
    "Low-Eガラス": "Kính Low-E",
    "複層ガラス": "Kính nhiều lớp",
    # bếp & thiết bị
    "食器洗い乾燥機": "Máy rửa & sấy bát",
    "ディスポーザー": "Máy nghiền rác thực phẩm",
    "生ゴミディスポーザー": "Máy nghiền rác thực phẩm",
    "IHクッキングヒーター": "Bếp từ",
    "ガスコンロ": "Bếp gas",
    "レンジフード": "Máy hút mùi",
    "浄水器": "Máy lọc nước",
    "人造大理石": "Đá nhân tạo",
    "天然石": "Đá tự nhiên",
    "御影石": "Đá granite",
    # vệ sinh
    "ミストサウナ": "Xông hơi ướt",
    "浴室暖房乾燥機": "Máy sưởi & sấy phòng tắm",
    "追い焚き機能": "Chức năng hâm nóng lại nước tắm",
    "温水洗浄便座": "Bồn cầu có vòi rửa",
    "洗面化粧台": "Bàn lavabo trang điểm",
    "タンクレストイレ": "Bồn cầu không két nước",
    # điện, smart home, an ninh
    "床暖房": "Sàn sưởi",
    "全館空調": "Điều hoà trung tâm toàn nhà",
    "24時間換気システム": "Hệ thống thông gió 24 giờ",
    "オートロック": "Khoá tự động sảnh",
    "ハンズフリーキー": "Chìa khoá không cần chạm",
    "防犯カメラ": "Camera an ninh",
    "TVモニター付インターホン": "Chuông hình có màn hình",
    "24時間有人管理": "Quản lý trực 24 giờ",
    "宅配ボックス": "Tủ nhận hàng",
    "EV充電設備": "Trạm sạc xe điện",
    # tiện ích
    "ラウンジ": "Sảnh nghỉ",
    "ゲストルーム": "Phòng lưu trú khách",
    "パーティールーム": "Phòng tiệc",
    "スカイラウンジ": "Sảnh nghỉ tầng cao",
    "フィットネスルーム": "Phòng tập thể hình",
    "フィットネス": "Phòng tập thể hình",
    "キッズルーム": "Phòng trẻ em",
    "スタディルーム": "Phòng học",
    "ワークスペース": "Không gian làm việc",
    "コワーキングスペース": "Không gian làm việc chung",
    "ライブラリー": "Thư viện",
    "コンシェルジュ": "Lễ tân concierge",
    "トランクルーム": "Kho riêng",
    "駐輪場": "Bãi để xe đạp",
    "駐車場": "Bãi đỗ xe",
    "機械式駐車場": "Bãi đỗ xe cơ khí",
    "屋上庭園": "Vườn trên mái",
    "中庭": "Sân trong",
    "プール": "Bể bơi",
    "ペット飼育可": "Cho phép nuôi thú cưng",
    "ドッグラン": "Sân chơi cho chó",
    "ゴミ置場": "Khu tập kết rác",
    "エントランスホール": "Sảnh vào",
    "ラウンジスペース": "Không gian sảnh nghỉ",
    # phòng & mặt bằng
    "ウォークインクローゼット": "Tủ áo đi vào",
    "シューズインクローゼット": "Tủ giày đi vào",
    "サービスルーム": "Phòng đa năng",
    "納戸": "Kho",
    "バルコニー": "Ban công",
    "ルーフバルコニー": "Ban công trên mái",
    "専用庭": "Sân vườn riêng",
    "角住戸": "Căn góc",
    # hướng
    "南向き": "Hướng Nam", "北向き": "Hướng Bắc",
    "東向き": "Hướng Đông", "西向き": "Hướng Tây",
    "南東向き": "Hướng Đông Nam", "南西向き": "Hướng Tây Nam",
    "北東向き": "Hướng Đông Bắc", "北西向き": "Hướng Tây Bắc",
    # Bổ sung theo bộ từ khoá mở rộng của rules.py
    "システムキッチン": "Bếp liên hoàn",
    "カウンターキッチン": "Bếp quầy",
    "天然石カウンター": "Mặt bàn bếp đá tự nhiên",
    "節水シャワー": "Bát sen tiết kiệm nước",
    "シャワー水栓": "Vòi sen",
    "手洗いカウンター": "Bàn rửa tay",
    "ユニットバス": "Phòng tắm liền khối",
    "フローリング": "Sàn gỗ",
    "高遮音床": "Sàn cách âm",
    "対震ドア枠": "Khung cửa chống động đất",
    "網戸": "Cửa lưới chống muỗi",
    "玄関収納": "Tủ giày sảnh căn hộ",
    "火災感知器": "Đầu báo cháy",
    "スプリンクラー": "Hệ thống chữa cháy tự động",
    "防災備品": "Vật tư phòng chống thiên tai",
    "非常用発電機": "Máy phát điện dự phòng",
    "太陽光発電": "Điện mặt trời",
    "電気自動車充電設備": "Trạm sạc xe điện",
    "ブロードバンド": "Hạ tầng internet băng thông rộng",
    "インターネット": "Hạ tầng internet",
    "洗濯機置場": "Chỗ đặt máy giặt",
    "スマートロック": "Khoá thông minh",
    "スマートホーム": "Nhà thông minh",
    "制振": "Kết cấu giảm chấn",
    "フロントサービス": "Dịch vụ lễ tân sảnh",
    "コーチエントランス": "Lối đón trả khách bằng ô tô",
    "クリーンステーション": "Điểm đổ rác từng tầng",
    "ゴミ置場": "Khu tập kết rác",
    "防災倉庫": "Kho vật tư phòng chống thiên tai",
    "自転車置場": "Bãi để xe đạp",
    "バイク置場": "Bãi để xe mô tô",
    "カーシェア": "Dịch vụ xe dùng chung",
    "保育所": "Nhà trẻ", "保育園": "Nhà trẻ",
    "デイサービス": "Cơ sở chăm sóc ban ngày",
    "会議室": "Phòng họp",
    "キッチンスタジオ": "Bếp studio",
    "カラオケ": "Phòng karaoke",
    "音楽室": "Phòng nhạc",
    "ゴルフレンジ": "Sân tập golf",
    "ペット飼育可": "Cho phép nuôi thú cưng",
}

# ── Quy tắc mẫu (chạy trước từ điển) ────────────────────────────────────────
# Những chuỗi lặp nhiều nhất không phải TỪ VỰNG mà là MẪU: `27階`, `34階`, `462戸`,
# `2026年3月`… Nhồi từng cái vào từ điển là vô hạn và tốn một lượt dịch cho mỗi
# con số. Một regex xử lý xong mọi tầng, mọi số căn, mãi mãi.
# Chữ số kanji phải quy về chữ số Ả Rập TRƯỚC mọi regex khác, vì địa chỉ Nhật
# viết `南青山一丁目` chứ không phải `南青山1丁目`.
_KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_SEQ = re.compile(r"([一二三四五六七八九十]+)(?=丁目|番|階|戸|号)")


def _kanji_to_int(text: str) -> str:
    """`一` → 1 · `十` → 10 · `二十三` → 23. Chỉ đụng vào số đứng trước lượng từ."""
    def one(m: "re.Match") -> str:
        s, total, tens = m.group(1), 0, 0
        for ch in s:
            if ch == "十":
                tens = max(1, tens or total or 1)
                total = 0
            else:
                total += _KANJI_NUM.get(ch, 0)
        return str(tens * 10 + total if tens else total)
    return _KANJI_SEQ.sub(one, text)


RULES: list = [
    # Ghi chú trong ngoặc phải bóc TRƯỚC, không thì các luật sau nhìn `他(地番)`
    # tưởng `他` còn nằm giữa chuỗi và bỏ qua.
    (re.compile(r"[(（](?:地番|地名地番|住居表示)[)）]"), ""),
    # Cụm ghép phải đứng TRƯỚC luật đơn lẻ, không thì `地下1階` khớp trước và phần
    # `付42階建` còn lại thành rác: `tầng hầm 1付42 tầng`.
    (re.compile(r"地下\s*(\d+)\s*階付\s*(\d+)\s*階建"), r"\2 tầng nổi, \1 tầng hầm"),
    (re.compile(r"地上\s*(\d+)\s*階\s*地下\s*(\d+)\s*階建?"), r"\1 tầng nổi, \2 tầng hầm"),
    (re.compile(r"地下\s*(\d+)\s*階建?"), r"\1 tầng hầm"),
    (re.compile(r"地上\s*(\d+)\s*階建?"), r"\1 tầng nổi"),
    (re.compile(r"(\d+)\s*階建"), r"\1 tầng"),
    (re.compile(r"(\d+)\s*階"), r"tầng \1"),
    (re.compile(r"屋上"), "mái"),
    (re.compile(r"(\d+)\s*戸"), r"\1 căn"),
    (re.compile(r"(\d+)\s*台"), r"\1 chỗ"),
    (re.compile(r"徒歩\s*(\d+)\s*分"), r"đi bộ \1 phút"),
    (re.compile(r"((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月"), r"tháng \2/\1"),
    (re.compile(r"((?:19|20)\d{2})\s*年"), r"năm \1"),
    (re.compile(r"(\d+)\s*丁目"), r"\1-chome"),
    # `49番1` là số thửa kép → `49-1`; phải đứng trước luật `番` đơn.
    (re.compile(r"(\d+)\s*番\s*(\d+)"), r"\1-\2"),
    (re.compile(r"(\d+)\s*番地?"), r"số \1"),
    (re.compile(r"他\s*$"), " và lân cận"),
    (re.compile(r"上旬"), " đầu tháng"),
    (re.compile(r"中旬"), " giữa tháng"),
    (re.compile(r"下旬"), " cuối tháng"),
    (re.compile(r"約(?=\s*\d)"), "khoảng "),
    (re.compile(r"株式会社|有限会社|合同会社"), ""),      # hậu tố pháp nhân, bỏ cho gọn
    (re.compile(r"一級建築士事務所"), "văn phòng kiến trúc sư"),
    (re.compile(r"東京都"), "Tokyo "),
    (re.compile(r"予定"), " (dự kiến)"),
    (re.compile(r"竣工"), "hoàn công"),
    (re.compile(r"入居"), "nhận nhà"),
    (re.compile(r"引き?渡し"), "bàn giao"),
    (re.compile(r"販売"), "mở bán"),
    (re.compile(r"完成"), "hoàn thành"),
]

# 23 quận Tokyo — tập đóng, xuất hiện ở gần như mọi toà trong danh sách.
WARDS = {
    "千代田区": "Quận Chiyoda", "中央区": "Quận Chuo", "港区": "Quận Minato",
    "新宿区": "Quận Shinjuku", "文京区": "Quận Bunkyo", "台東区": "Quận Taito",
    "墨田区": "Quận Sumida", "江東区": "Quận Koto", "品川区": "Quận Shinagawa",
    "目黒区": "Quận Meguro", "大田区": "Quận Ota", "世田谷区": "Quận Setagaya",
    "渋谷区": "Quận Shibuya", "中野区": "Quận Nakano", "杉並区": "Quận Suginami",
    "豊島区": "Quận Toshima", "北区": "Quận Kita", "荒川区": "Quận Arakawa",
    "板橋区": "Quận Itabashi", "練馬区": "Quận Nerima", "足立区": "Quận Adachi",
    "葛飾区": "Quận Katsushika", "江戸川区": "Quận Edogawa",
}

# Chủ đầu tư / tổng thầu lớn — cũng là tập đóng, lặp qua hàng chục toà.
COMPANIES = {
    "住友不動産": "Sumitomo Realty & Development", "三井不動産レジデンシャル": "Mitsui Fudosan Residential",
    "三菱地所レジデンス": "Mitsubishi Jisho Residence", "東京建物": "Tokyo Tatemono",
    "野村不動産": "Nomura Real Estate", "大和ハウス工業": "Daiwa House Industry",
    "住友商事": "Sumitomo Corporation", "東急不動産": "Tokyu Land",
    "旭化成ホームズ": "Asahi Kasei Homes", "近鉄不動産": "Kintetsu Real Estate",
    "コスモスイニシア": "Cosmos Initia", "オープンハウス": "Open House",
    "三井住友建設": "Sumitomo Mitsui Construction", "鹿島建設": "Kajima",
    "大成建設": "Taisei", "清水建設": "Shimizu", "大林組": "Obayashi",
    "竹中工務店": "Takenaka", "前田建設工業": "Maeda", "西松建設": "Nishimatsu Construction",
    "五洋建設": "Penta-Ocean Construction", "岩田地崎建設": "Iwata Chizaki",
    "長谷工コーポレーション": "Haseko", "大建設計": "Daiken Sekkei",
}


# Sau khi thay, `Quận Minato` dính liền `南青山` thành một khối không đọc được.
_GLUE_A = re.compile(r"(?<=[A-Za-zÀ-ỹ0-9])(?=[぀-ヿ㐀-䶿一-鿿가-힯])")
_GLUE_B = re.compile(r"(?<=[぀-ヿ㐀-䶿一-鿿가-힯])(?=[A-Za-zÀ-ỹ0-9])")


def apply_rules(text: str) -> str:
    text = _kanji_to_int(text)
    for pattern, repl in RULES:
        # Đệm khoảng trắng quanh MỌI thay thế rồi thu gọn ở cuối. Không đệm thì hai
        # luật kề nhau dính thành `tháng 3/2026hoàn công`; đệm rồi thu gọn xử lý
        # được cả trường hợp xoá (repl rỗng) mà không phải liệt kê từng luật.
        text = pattern.sub(f" {repl} " if repl else " ", text)
    text = _GLUE_B.sub(" ", _GLUE_A.sub(" ", text))
    text = re.sub(r"\s+([,.);])", r"\1", re.sub(r"\s{2,}", " ", text))
    return text.strip(" ・,")


SEED.update({
    "撮影": "chụp", "外観": "Phối cảnh ngoại thất", "概念図": "Sơ đồ minh hoạ",
    "各戸検針方式": "Đồng hồ đo riêng từng căn", "専有部電気錠": "Khoá điện căn hộ",
    "スライド式": "kiểu trượt", "全住戸標準採用": "trang bị tiêu chuẩn toàn bộ căn hộ",
    "標準装備": "trang bị tiêu chuẩn", "地番": "số thửa", "権利形態": "hình thức sở hữu",
    "所有権": "quyền sở hữu", "管理形態": "hình thức quản lý", "用途地域": "phân khu chức năng",
})
SEED.update(WARDS)
SEED.update(COMPANIES)

_auto: Optional[Dict[str, str]] = None
_misses: Dict[str, int] = {}
_lock = threading.Lock()


def _load_auto() -> Dict[str, str]:
    global _auto
    if _auto is None:
        try:
            data = json.loads(AUTO_PATH.read_text(encoding="utf-8"))
            _auto = {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}
        except (OSError, json.JSONDecodeError):
            _auto = {}
    return _auto


def vi(term: object, default: Optional[str] = None) -> str:
    """Dịch một thuật ngữ. Trượt từ điển → giữ nguyên văn và ghi nhận."""
    s = normalize(term).strip()
    if not s:
        return default or ""
    table = _load_auto()
    hit = SEED.get(s) or table.get(s)
    if hit:
        return hit
    if not CJK.search(s):
        return s                       # đã là chữ La-tinh, không cần dịch
    with _lock:
        _misses[s] = _misses.get(s, 0) + 1
    return default if default is not None else s


# Mảnh chữ gốc còn sót sau khi dịch. Ghi nhận TỪNG MẢNH thay vì cả câu là thay
# đổi quan trọng nhất: `東京都港区南青山一丁目49番1他` dịch một lần rồi bỏ (địa chỉ
# không lặp), nhưng mảnh `南青山` học một lần là dùng được cho mọi toà cùng khu.
# `-` phải được coi là NẰM TRONG mảnh: bước crawl biến dấu trường âm katakana `ー`
# thành `-` ASCII, nên `カウンタートップ` tới đây là `カウンタ-トップ`. Không cho `-`
# vào thì mảnh bị cắt đôi thành `カウンタ` + `トップ` — hai mẩu vô nghĩa, dịch xong
# cũng không dùng được.
RESIDUAL = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+(?:-[぀-ヿ㐀-䶿一-鿿가-힯]+)*")
MAX_MISS_LEN = 30                      # dài hơn thế gần như luôn là rác trích nhầm
# Dấu phân cách, không phải từ — gửi đi dịch chỉ tốn chỗ trong lô.
PUNCT_ONLY = re.compile(r"^[・、。〜~ー\-–—…]+$")

# Chuẩn hoá dấu trường âm bị crawl làm hỏng, để tra từ điển trúng: từ điển viết
# `カウンタートップ` còn văn bản tới đây là `カウンタ-トップ`.
_LONG_VOWEL = re.compile(r"(?<=[ァ-ヿ])-(?=[ァ-ヿ])")


def normalize(text: str) -> str:
    return _LONG_VOWEL.sub("ー", str(text or ""))


MAX_PHRASE_LEN = 40                    # dài hơn thế coi là CÂU, không phải thuật ngữ


def _note_residual(text: str, source_len: int) -> None:
    """Ghi nhận phần chưa dịch được, tách mảnh hay giữ nguyên câu tuỳ độ dài nguồn.

    Cắt mảnh chỉ đúng với trường NGẮN (địa chỉ, tên riêng): `南青山` học một lần
    dùng cho mọi toà cùng khu. Với câu dài thì cắt mảnh cho ra `バスルームには`,
    `を採用` — đó là mẩu ngữ pháp giữa câu, không phải thuật ngữ, dịch xong cũng
    không tra lại được bao giờ. Câu dài thì ghi cả câu.
    """
    with _lock:
        if source_len > MAX_PHRASE_LEN:
            if source_len <= 160:      # dài hơn nữa gần như luôn là rác trích nhầm
                _misses[text] = _misses.get(text, 0) + 1
            return
        for frag in RESIDUAL.findall(text):
            if len(frag) <= MAX_MISS_LEN and not PUNCT_ONLY.match(frag):
                _misses[frag] = _misses.get(frag, 0) + 1


def vi_phrase(text: object) -> str:
    """Dịch một chuỗi: quy tắc mẫu → tra nguyên câu → thay cụm dài nhất đã biết.

    Không phải dịch máy. Phần chữ gốc còn sót được ghi nhận theo TỪNG MẢNH để
    bước translate học được token dùng lại được, thay vì học thuộc cả câu.
    """
    s = normalize(text).strip()
    if not s or not CJK.search(s):
        return s
    exact = SEED.get(s) or _load_auto().get(s)
    if exact:
        return exact

    out = apply_rules(s)
    exact = SEED.get(out) or _load_auto().get(out)
    if exact:
        return exact
    for term in sorted(set(SEED) | set(_load_auto()), key=len, reverse=True):
        if term in out:
            out = out.replace(term, (SEED.get(term) or _load_auto()[term]))
    out = _GLUE_B.sub(" ", _GLUE_A.sub(" ", out))
    out = re.sub(r"\s{2,}", " ", out).strip(" ・,")
    if CJK.search(out):
        _note_residual(out, len(s))
        if ROMAJI_FALLBACK:
            out = RESIDUAL.sub(lambda m: f"~{romaji(m.group(0))}" or m.group(0), out)
            out = re.sub(r"\s{2,}", " ", out).strip()
    return out or s


# pykakasi chuyển kanji/kana → romaji hoàn toàn ngoại tuyến, không cần model.
# Nhưng nó KHÔNG biết cách đọc riêng của địa danh: 月島 nó đọc `Gatsu Shima` trong
# khi tên thật là Tsukishima, 勝どき → `Kachi Doki` thay vì Kachidoki. Đo trên 7
# địa danh Tokyo chỉ đúng 4. Nên đây chỉ là lưới chót cho những gì LLM chưa kịp
# dịch — bật bằng WS1_ROMAJI_FALLBACK=1, và mọi giá trị do nó sinh ra đều bị đánh
# dấu `~` để người đọc CSV biết là phiên âm máy, chưa ai xác nhận.
ROMAJI_FALLBACK = os.environ.get("WS1_ROMAJI_FALLBACK", "") == "1"
_kks = None


def romaji(text: str) -> str:
    """Phiên âm La-tinh gần đúng. Trả chuỗi rỗng nếu không dùng được pykakasi."""
    global _kks
    if _kks is None:
        try:
            import pykakasi
            _kks = pykakasi.kakasi()
        except ImportError:
            _kks = False
    if not _kks:
        return ""
    try:
        return " ".join(x["hepburn"].capitalize() for x in _kks.convert(text) if x["hepburn"])
    except Exception:
        return ""


def misses() -> Dict[str, int]:
    with _lock:
        return dict(_misses)


def flush_misses() -> Path:
    """Ghi các term chưa dịch ra đĩa (gộp với lần chạy trước) cho bước translate."""
    with _lock:
        current = dict(_misses)
    try:
        old = json.loads(MISS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        old = {}
    for term, count in current.items():
        old[term] = old.get(term, 0) + count
    MISS_PATH.write_text(json.dumps(old, ensure_ascii=False, indent=2, sort_keys=True),
                         encoding="utf-8")
    return MISS_PATH


def merge_auto(pairs: Dict[str, str]) -> int:
    """Ghi kết quả dịch gộp vào lexicon_auto.json. Trả số mục MỚI thêm được."""
    table = dict(_load_auto())
    added = 0
    for term, viet in pairs.items():
        term, viet = str(term).strip(), str(viet).strip()
        if not term or not viet or term in table:
            continue
        if viet == term or CJK.search(viet):
            continue                   # model chép lại nguyên văn / dịch dở — bỏ
        table[term] = viet
        added += 1
    AUTO_PATH.write_text(json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True),
                         encoding="utf-8")
    global _auto
    _auto = table
    return added
