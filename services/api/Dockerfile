# Ảnh gọn, chạy bằng người dùng thường — không có lý do gì để root ở đây.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Cài phụ thuộc trước, tách khỏi mã nguồn để lớp này còn được dùng lại khi sửa code.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# uid 1001 khớp với `lanch` trên host, để file mount vào đọc được mà không cần root.
RUN useradd -u 1001 -m -s /usr/sbin/nologin api \
 && mkdir -p /tmp/duckdb-spill && chown api:api /tmp/duckdb-spill
USER api

EXPOSE 8000

# Một worker là đủ: DuckDB dùng nhiều luồng cho một truy vấn, thêm worker chỉ tổ
# chia nhau 8 nhân. Cần chịu tải hơn thì tăng ở đây sau khi đo, đừng đoán.
# `--no-access-log`: app tự ghi log, có giờ và thời lượng — xem `stamp_and_log`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-access-log"]
