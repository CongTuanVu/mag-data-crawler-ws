#!/usr/bin/env bash
# WS1 Building — chạy trọn ba chặng: crawl → sinh code bóc tách → ghi CSV.
#
#   ./run_all.sh                  cả ba chặng, dùng buildings.txt
#   ./run_all.sh -n               chỉ in lệnh sẽ chạy, không chạy (thử trước)
#   ./run_all.sh --skip-crawl     đã crawl rồi, chỉ bóc tách + ghi CSV lại
#   ./run_all.sh --rebuild        sinh lại code_extract/rules.py (tốn 1 lượt model)
#   ./run_all.sh --vision         chạy thêm vision đọc bản vẽ để có B3 unit_room
#
# Cờ:
#   -i, --input FILE      danh sách toà nhà            (mặc định buildings.txt)
#   -w, --workers N       số tiến trình bóc tách       (mặc định 8)
#       --batch-size N    số toà crawl song song       (mặc định 4)
#       --batch-sleep N   giây nghỉ giữa hai lô crawl  (mặc định 30)
#       --on-rate-limit X stop | wait | continue       (mặc định stop)
#       --skip-crawl      bỏ chặng 1
#       --skip-translate  bỏ bước dịch gộp + ghi CSV lượt hai
#       --rebuild         ép sinh lại code bóc tách
#       --vision          chạy thêm chặng vision cho B3
#   -n, --dry-run         in lệnh, không chạy
#   -h, --help            trợ giúp này
#
# Biến môi trường: PYTHON=python3.11 ./run_all.sh   (đổi trình thông dịch)
#
# Chạy lại an toàn ở bất kỳ điểm nào: chặng crawl bỏ qua toà đã có manifest.json,
# chặng bóc tách chạy lại cũng chỉ mất ~30 giây.
set -uo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
INPUT="buildings.txt"
WORKERS=8
BATCH_SIZE=4
BATCH_SLEEP=30
ON_RATE_LIMIT="stop"
DO_CRAWL=1; DO_BUILD=1; DO_EXTRACT=1; DO_TRANSLATE=1; DO_VISION=0
REBUILD=0; DRY_RUN=0

usage() { awk 'NR>1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "$0"; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    -i|--input)      INPUT="$2"; shift 2 ;;
    -w|--workers)    WORKERS="$2"; shift 2 ;;
    --batch-size)    BATCH_SIZE="$2"; shift 2 ;;
    --batch-sleep)   BATCH_SLEEP="$2"; shift 2 ;;
    --on-rate-limit) ON_RATE_LIMIT="$2"; shift 2 ;;
    --skip-crawl)    DO_CRAWL=0; shift ;;
    --skip-translate) DO_TRANSLATE=0; shift ;;
    --rebuild)       REBUILD=1; shift ;;
    --vision)        DO_VISION=1; shift ;;
    -n|--dry-run)    DRY_RUN=1; shift ;;
    -h|--help)       usage ;;
    *) echo "cờ không hiểu: $1 (xem --help)" >&2; exit 2 ;;
  esac
done

START=$(date +%s)
CRAWL_RC=0
banner() { printf '\n\033[1m%s\033[0m\n%s\n' "$1" "$(printf '═%.0s' $(seq 1 72))"; }
step()   { echo "  \$ $*"; [ "$DRY_RUN" = 1 ] || "$@"; }

[ -f "$INPUT" ] || { echo "không thấy file danh sách: $INPUT" >&2; exit 2; }
[ "$DRY_RUN" = 1 ] && echo "── CHẾ ĐỘ THỬ: chỉ in lệnh, không chạy ──"

# ── Chặng 1: crawl ──────────────────────────────────────────────────────────
if [ "$DO_CRAWL" = 1 ]; then
  banner "[1/4] Crawl raw về output_raw/  (chặng duy nhất còn gọi model — bước tìm nguồn)"
  step "$PY" run.py --input "$INPUT" --crawl-only --no-shots --skip-done \
       --batch-size "$BATCH_SIZE" --batch-sleep "$BATCH_SLEEP" \
       --on-rate-limit "$ON_RATE_LIMIT"
  CRAWL_RC=$?
  if [ "$CRAWL_RC" != 0 ]; then
    echo "⚠ chặng crawl dừng sớm (mã $CRAWL_RC) — vẫn bóc tách phần đã crawl được."
    echo "  Chạy lại chính ./run_all.sh sau để crawl nốt phần còn thiếu."
  fi
fi

# ── Chặng 2: sinh code bóc tách ─────────────────────────────────────────────
if [ "$DO_BUILD" = 1 ]; then
  banner "[2/4] Sinh code bóc tách  (1 lượt agent đọc cấu trúc HTML)"
  if [ -f code_extract/rules.py ] && [ "$REBUILD" = 0 ]; then
    echo "  ↷ đã có code_extract/rules.py — bỏ qua. Muốn sinh lại: ./run_all.sh --rebuild"
    echo "    (bản cũ luôn được sao lưu ở code_extract/.bak/ trước khi ghi đè)"
  else
    step "$PY" run_extract.py build || echo "⚠ sinh code lỗi — dùng bản rules.py hiện có"
  fi
fi

# ── Chặng 3: bóc tách → CSV ─────────────────────────────────────────────────
if [ "$DO_EXTRACT" = 1 ]; then
  banner "[3/4] Bóc tách bằng code → output_csv/  (không gọi model)"
  step "$PY" run_extract.py run --workers "$WORKERS"

  # Dịch xong phải chạy lại chặng 3: từ điển được tra LÚC bóc tách, nên CSV vừa
  # ghi vẫn giữ thuật ngữ gốc. Lượt hai chỉ mất ~30 giây.
  if [ "$DO_TRANSLATE" = 1 ]; then
    banner "[4/4] Dịch gộp thuật ngữ còn sót, rồi ghi lại CSV"
    if step "$PY" run_extract.py translate; then
      step "$PY" run_extract.py run --workers "$WORKERS"
    else
      echo "⚠ bước dịch lỗi — CSV vẫn dùng được, thuật ngữ lạ giữ nguyên văn"
    fi
  fi
fi

# ── Tuỳ chọn: vision đọc bản vẽ cho B3 ──────────────────────────────────────
if [ "$DO_VISION" = 1 ]; then
  banner "[+] Vision đọc ảnh mặt bằng → B3 unit_room  (gọi model, mỗi ảnh 1 lượt)"
  step "$PY" run.py --input "$INPUT" --skip-discover --skip-crawl --skip-extract
fi

TOOK=$(( $(date +%s) - START ))
banner "Xong sau $((TOOK / 60))m$((TOOK % 60))s"
if [ "$DRY_RUN" = 0 ]; then
  echo "  CSV:  $(ls output_csv/*.csv 2>/dev/null | wc -l | tr -d ' ') file trong output_csv/"
  echo "  Raw:  $(ls -d output_raw/*/ 2>/dev/null | wc -l | tr -d ' ') toà trong output_raw/"
  [ "$CRAWL_RC" != 0 ] && echo "  ⚠ crawl chưa xong hết — chạy lại ./run_all.sh để tiếp tục"
fi
exit 0
