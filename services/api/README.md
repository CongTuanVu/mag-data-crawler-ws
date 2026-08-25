# services/api — API dữ liệu ws1

FastAPI đứng trước DuckDB. Thay cho cách cũ: nhúng toàn bộ dữ liệu vào một file
HTML 4 MB, mà trong đó UI chỉ chạm được **0,04%** kho (250 toà mẫu mỗi thị trường
trên 618.421 toà thật).

## Hai chế độ

| `DATA_MODE` | Nguồn | Dùng khi |
|---|---|---|
| `mock` *(mặc định)* | `app/mock.py`, số giả sinh từ hạt cố định | dựng hạ tầng, làm frontend |
| `real` | parquet qua DuckDB (`app/queries.py`, `app/vn.py`) | khi đã nối xong dữ liệu |

Hình dạng phản hồi **giống hệt** ở hai chế độ, nên đổi là đổi một biến môi trường.
Mọi phản hồi mang `"mock": true|false` và header `X-Data-Mode` — đừng gỡ, nếu không
ảnh chụp màn hình sẽ bị đọc nhầm là số thật.

## Chạy

```bash
docker compose up -d --build      # từ gốc repo
curl -s localhost:8040/health
docker compose logs -f api
```

Không có Docker thì chạy trực tiếp:

```bash
pip install -r requirements.txt
DATA_MODE=mock uvicorn app.main:app --port 8040
```

Tài liệu tương tác: `/docs` (OpenAPI sinh sẵn).

## Endpoint

| Đường dẫn | Trả về |
|---|---|
| `GET /health` | trạng thái + chế độ; `503` khi `real` mà không đọc được parquet |
| `GET /markets` | 22 thị trường, kèm số toà và mấy trường lõi đạt |
| `GET /markets/{slug}` | meta, sáu trường lõi, độ phủ, danh sách loại hình |
| `GET /markets/{slug}/buildings` | `?q= &form= &sort= &limit= &offset=` — duyệt thật, có phân trang |
| `GET /markets/{slug}/metrics` | `?form=` — phân bố tính LẠI theo bộ lọc đang chọn |
| `GET /buildings/{code}` | một toà |
| `GET /vn/projects` | `?province= &category= &q= &sort= &limit= &offset=` |
| `GET /vn/categories` | loại dự án, kèm `slug` để dùng cho `?category=` |
| `GET /vn/projects/{id}` | dự án + toà thuộc dự án + thống kê tin rao |
| `GET /vn/provinces` | thống kê từng tỉnh |
| `GET /vn/tiers` | thang bốn cấp, đo theo **cha trực tiếp** |
| `GET /overview` | số tổng quan ba kho |
| `GET /docs/search` | `?q=` — toàn văn 22.559 tài liệu *(chỉ `mock`, xem bên dưới)* |

`sort` nhận: `full · units · floors · year · area · price · name`
(bảng Việt Nam: `full · units · floors · site · name`).
`limit` chặn ở `MAX_PAGE_SIZE` (mặc định 200) — vượt thì `422`, không im lặng cắt.

### Định danh là slug ASCII, không phải chữ có dấu

`province` và `category` nhận **slug**, lấy từ `/vn/provinces` và `/vn/categories`:

```
?province=ha-noi          không phải  ?province=H%C3%A0%20N%E1%BB%99i
?province=ho-chi-minh
?category=khu-cong-nghiep
```

Mỗi bản ghi trả về mang cả `slug` (để lọc) lẫn nhãn có dấu (để hiển thị).
Nhãn có dấu vẫn được chấp nhận nên client cũ không gãy.

Slug **không tra được thì trả `422`** kèm gợi ý, chứ không lặng lẽ bỏ bộ lọc —
bỏ qua trong im lặng nghĩa là hỏi Hà Nội mà nhận về cả kho, không có dấu hiệu nào.

Cái bẫy khi tự sinh slug: `strip_accents('đường Đông')` cho ra `đuong Đong` —
bỏ dấu thanh nhưng **giữ nguyên `đ`**. Thiếu bước thay `đ`→`d` thì `da-nang`
không khớp `Đà Nẵng`. Xem `app/slugs.py`.

`q` vẫn là chữ tự do người dùng gõ, nên **phải `encodeURIComponent`** ở phía
client; gửi UTF-8 thô trong URL thì uvicorn trả `400` ngay ở dòng request.

## Chưa xong ở chế độ `real`

- `/overview` và `/docs/search` trả **501**. Truy vấn FTS5 đã thử tay và chạy
  (6 ms cho join đủ), nhưng bảng `fts` là `content=''` nên `snippet()` trả rỗng —
  muốn có đoạn trích phải đọc từ `md_file/` theo `chunks.off/len`. Chưa nối.
- **Đường dẫn bộ tài liệu vừa đổi**: `/srv/ws1/data/vinhhd/` không còn, dữ liệu
  nay ở `/mnt/data/ws1-data/vinhhd/`. `code_ui/build_overview.py` vẫn trỏ đường cũ
  nên phần đếm tài liệu ở trang tổng quan sẽ **âm thầm biến mất** ở lần build tới.
- Nhật Bản (`output_csv/`) chưa có đường đọc trong `real`.

## Nợ kỹ thuật

`app/corpus_gate.py` đang **trùng** định nghĩa sáu trường lõi với
`code_ui/build_market.py`. Hai bản lệch nhau là số trên trang và số trong API khác
nhau mà không ai biết. Cần gộp về một chỗ, cho builder import từ đây.

## Vì sao không cần database

DuckDB đọc thẳng parquet, không ETL, không nạp trước. Đo trên máy này
(8 nhân, toàn bộ parquet 133 MB):

```
lọc 1 thị trường + sắp xếp, lấy 50 toà       23,5 ms
phân vị 4 chỉ tiêu trên toàn 618k dòng       29,5 ms
tin rao VN: lọc theo dự án + phân vị giá     18,6 ms
quét ILIKE tên toà toàn kho                 158,2 ms   ← chậm nhất
```
