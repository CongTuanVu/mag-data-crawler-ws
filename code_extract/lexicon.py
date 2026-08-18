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
    s = str(term or "").strip()
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


def vi_phrase(text: object) -> str:
    """Dịch cả câu bằng cách thay từng thuật ngữ dài nhất đã biết.

    Không phải dịch máy — chỉ thay cụm đã có trong từ điển, phần còn lại giữ
    nguyên và được ghi nhận để bước translate xử lý gọn một lượt.
    """
    s = str(text or "").strip()
    if not s or not CJK.search(s):
        return s
    exact = SEED.get(s) or _load_auto().get(s)
    if exact:
        return exact
    out = s
    for term in sorted(set(SEED) | set(_load_auto()), key=len, reverse=True):
        if term in out:
            out = out.replace(term, (SEED.get(term) or _load_auto()[term]))
    if CJK.search(out):
        with _lock:
            _misses[s] = _misses.get(s, 0) + 1
    return out


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
        table[term] = viet
        added += 1
    AUTO_PATH.write_text(json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True),
                         encoding="utf-8")
    global _auto
    _auto = table
    return added
