#!/usr/bin/env bash
# Dựng lại trang thị trường từ corpus và để systemd tự đẩy lên /ws1-data/.
#
#   code_ui/refresh_market.sh                 # mặc định: 250 toà mẫu / thị trường
#   code_ui/refresh_market.sh --sample 400    # sâu hơn, file to hơn
#
# CHỈ dựng bản GỌI API (payload rỗng, ~210 KB) rồi để systemd đẩy lên /ws1-data/.
# Bản tự chứa thì chạy tay và nó ra `dist/export.html`, KHÔNG vào đường deploy:
#
#   python3 code_ui/build_market.py --export
#
# Builder từ chối nếu `--export` bị trỏ vào `dist/index.html`.
#
# `ws1-data-sync.path` theo dõi code_ui/dist/index.html nên không cần copy tay.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORPUS="${STRUCTURED_CORPUS_DIR:-/mnt/data/ws1-data/lanch}"
OUT="$HERE/dist/index.html"
PY="${PYTHON:-python3}"

[[ -d "$CORPUS" ]] || { echo "không thấy corpus: $CORPUS" >&2; exit 1; }
"$PY" -c 'import duckdb' 2>/dev/null || { echo "thiếu duckdb: pip install duckdb" >&2; exit 1; }

echo "corpus : $CORPUS"
stat -c '  parquet mới nhất: %y' "$CORPUS/corpus_loose.parquet"

case " $* " in
  *" --export "*) echo "refresh_market.sh chỉ dựng bản gọi API." >&2
                  echo "Bản tự chứa: python3 code_ui/build_market.py --export" >&2
                  exit 2 ;;
esac

"$PY" "$HERE/build_market.py" --corpus "$CORPUS" --out "$OUT" "$@"

echo
echo "đã ghi : $OUT  ($(du -h "$OUT" | cut -f1))"
if [[ -f /var/www/ws1-data/index.html ]]; then
  sleep 2
  if cmp -s "$OUT" /var/www/ws1-data/index.html; then
    echo "đã đẩy : /var/www/ws1-data/index.html  →  /ws1-data/"
  else
    echo "CHƯA đẩy — kiểm 'systemctl status ws1-data-sync.path'" >&2
  fi
fi
