# services/api — API dữ liệu ws1

FastAPI đứng trước DuckDB. Thay cho cách cũ: nhúng toàn bộ dữ liệu vào một file
HTML 4 MB, mà trong đó UI chỉ chạm được **0,04%** kho (250 toà mẫu mỗi thị trường
trên 618.421 toà thật).

## Hai chế độ

| `DATA_MODE` | Nguồn | Dùng khi |
|---|---|---|
| `mock` | `app/mock.py`, số giả sinh từ hạt cố định | dựng hạ tầng, làm frontend |
| `real` *(mặc định)* | parquet qua DuckDB + CSV Nhật + FTS5 tài liệu | chạy thật |

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
| `GET /docs/search` | `?q=` — toàn văn 22.559 tài liệu / 207.816 đoạn |

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

## Tốc độ

Đo trên máy này (8 nhân, parquet 133 MB), `real`, qua HTTP:

```
/health                                    4 ms
/markets            lần đầu 637 ms  →  gọi lại   2 ms   (cache theo mtime parquet)
/markets/korea                            14 ms
/markets/korea/buildings?sort=units       54 ms   trên 125.373 toà
/markets/korea/buildings?sort=full       203 ms   ← chậm nhất, xem dưới
/markets/korea/metrics                   178 ms
/vn/projects?province=ha-noi              36 ms
/vn/provinces                             17 ms
/overview                                 16 ms
/docs/search?q=…                           5 ms   trên 207.816 đoạn
/markets/japan/buildings                   3 ms   (nạp sẵn trong bộ nhớ)
```

Bốn quyết định, đều đo trước khi chọn:

**Gộp lượt quét.** `/markets` bản đầu lặp 20 thị trường × 13 truy vấn = ~260 lượt
quét 618k dòng. Giờ là MỘT truy vấn `group by market` với `count(*) filter (...)`.
`/metrics` từ hơn 100 lượt xuống 2: một lượt lấy bảy phân vị của cả sáu chỉ tiêu
(`quantile_cont` nhận danh sách mốc nên chỉ sắp xếp một lần), một lượt đếm mọi
khoảng.

**Chỉ chấm cổng strict cho dòng trả về.** `_core`/`_strict` là 16 vị từ mỗi dòng.
Chỉ `sort=full` cần chúng để SẮP XẾP; các kiểu khác sắp bằng cột thường rồi mới
chấm cho đúng 50 dòng. Hàn Quốc: 195 ms → 45–83 ms.

**Không đếm tổng khi không lọc.** Tổng đã có sẵn từ lượt quét gộp. Chỉ khi có
`q`/`form` mới đếm, và kết quả đếm được cache theo bộ lọc.

**Hai thứ đã thử và BỎ**, ghi lại để khỏi ai thử lại:

- *Nạp parquet vào bảng bộ nhớ*: tốn 912 MiB, truy vấn điểm nhanh hơn 13 ms
  nhưng truy vấn gộp **chậm hơn** (19,2 so với 15,6 ms). Parquet đã là cột có
  zone map.
- *`count(*) over ()` để gộp đếm vào cùng truy vấn*: **chậm gấp đôi**
  (451 so với 217 ms) vì buộc vật chất hoá toàn bộ dòng trước khi cắt.

## Chưa xong ở chế độ `real`

- **Không có đoạn trích trong kết quả tìm tài liệu.** Bảng FTS5 khai
  `content=''` nên nó không giữ lại nguyên văn — `snippet()` trả rỗng. Muốn có
  đoạn quanh từ khoá phải đọc file `.md` theo `chunks.off/len`. Trả về hiện có
  tiêu đề, tên miền, ngôn ngữ, URL.
- **Nhật chưa có phân bố**: `/markets/japan/metrics` trả mảng rỗng.
- **`code_ui/build_overview.py` vẫn trỏ đường dẫn tài liệu cũ.**
  `/srv/ws1/data/vinhhd/` không còn tồn tại, nay ở `/mnt/data/ws1-data/vinhhd/`.
  Trang tổng quan tĩnh sẽ mất phần đếm tài liệu ở lần build tới — hàm đó
  `return None` khi không thấy file, nên hỏng trong im lặng. API đã trỏ đúng.

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
