# code_proxy — chạy pipeline bằng Claude Code CLI, không cần API key

Một HTTP server localhost, không phụ thuộc thư viện ngoài, nói **Anthropic
Messages API** nhưng bên dưới gọi `claude --print`. Dùng phiên đăng nhập sẵn có
của CLI, nên chạy được pipeline mà không cần `ANTHROPIC_API_KEY`.

Đây là công cụ chạy thử, không phải model gateway cho production: mỗi request
khởi động một tiến trình CLI nên chậm hơn API thật và ăn vào hạn mức sử dụng
Claude của bạn.

## Chạy

Cần Python 3.9+ (cùng `python3` với crawler) và Claude Code CLI đã đăng nhập.

```bash
claude auth status            # phải thấy "loggedIn": true
```

**Terminal 1 — proxy:**

```bash
CLAUDE_PROXY_MODEL=claude-opus-5 ./code_proxy/start.sh --timeout 900
```

**Terminal 2 — crawler:**

```bash
unset ANTHROPIC_API_KEY
export ANTHROPIC_BASE_URL=http://127.0.0.1:11439

# Đầu vào duy nhất là buildings.txt — agent tự tìm nguồn
python3 run.py --input buildings.txt \
        --skip-vision --no-shots --skip-done \
        --batch-size 4 --batch-sleep 90
```

Muốn tự chỉ định nguồn thay vì để agent tìm thì thêm `--sources-dir sources`:
nhanh hơn và tái lập được, nhưng phải tự chuẩn bị một file cho mỗi toà.

Đặt `ANTHROPIC_BASE_URL` là `pipeline/config.py` tự bật `COMPAT`: ép JSON bằng
prompt rồi tự parse, bỏ `cache_control`.

## Tra web: bước `discover` chạy được

CLI có sẵn tool `WebSearch` / `WebFetch`. Khi request khai server tool
`web_search` / `web_fetch` — đúng như `pipeline/discover.py` làm — proxy mở đúng
hai tool đó cho tiến trình CLI:

```
--tools "WebSearch,WebFetch" --allowed-tools WebSearch WebFetch
```

Cần **cả hai** cờ: `--tools` chỉ làm tool *có sẵn*, còn `--permission-mode
dontAsk` vẫn từ chối lúc chạy nếu không có `--allowed-tools` duyệt trước.

Nhờ vậy `run.py` chạy được với đầu vào duy nhất là `buildings.txt`, không cần
`--sources-dir` hay `--skip-discover`.

Mọi request khác vẫn chạy với `--tools ""` — không mạng, không đọc file, không
chạy lệnh. Request khai loại tool nào khác `web_*` thì bị từ chối bằng HTTP 400:
endpoint này không chạy vòng lặp tool phía client.

## Hai giới hạn còn lại

| Thiếu | Hệ quả | Cách đi tiếp |
| --- | --- | --- |
| vision | bảng B3 `unit_room` sẽ rỗng | `--skip-vision` |
| structured output của API | JSON ép bằng prompt, thỉnh thoảng lệch | SDK đã retry sẵn 4 lần |

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `CLAUDE_PROXY_MODEL` | `claude-sonnet-5` | model khi request không nêu tên |
| `LLM_PROXY_PORT` / `--port` | `11439` | cổng lắng nghe |
| `LLM_PROXY_HOST` / `--host` | `127.0.0.1` | địa chỉ bind |
| `LLM_PROXY_TIMEOUT` / `--timeout` | `300` | giây cho mỗi request; đặt `900` cho corpus lớn |
| `LLM_PROXY_MAX_CONCURRENCY` | `4` | số tiến trình CLI đồng thời, mỗi cái ~400 MB RSS |
| `LLM_PROXY_MAX_BODY_MB` | `16` | trần kích thước body |
| `LLM_PROXY_API_KEY` | (tắt) | bearer/`x-api-key` bắt buộc; buộc phải có nếu bind ra ngoài loopback |
| `CLAUDE_CLI` | (tự tìm) | đường dẫn tuyệt đối tới `claude` |

`--batch-size` của `run.py` nên **≤** `LLM_PROXY_MAX_CONCURRENCY`, nếu không
request sẽ xếp hàng và dễ chạm timeout.

CLI được tìm theo thứ tự: `CLAUDE_CLI` → `claude` trên `PATH` → `~/.local/bin` →
`~/.claude/local` → extension Claude Code của VS Code / Cursor / Windsurf.

## Endpoint

- `POST /v1/messages` — Anthropic Messages API, **đường mà pipeline dùng**
- `POST /v1/chat/completions` — OpenAI Chat Completions, có function tools
- `POST /v1/responses` — OpenAI Responses
- `GET /v1/models` · `GET /healthz` · `GET /`

Trên `/v1/messages` chỉ hỗ trợ server tool `web_search` / `web_fetch`; mọi loại
tool khác, kể cả trên `/v1/responses`, đều bị từ chối. Không hỗ trợ ảnh, audio,
embedding, logprobs, nhiều lựa chọn. `temperature`, `top_p`, `max_tokens` nhận nhưng **bỏ qua** — CLI
không cho chỉnh.

## Kiểm tra nhanh

```bash
curl -s http://127.0.0.1:11439/healthz

python3 -m unittest discover -s code_proxy/tests -p 'test_*.py'
```

## Cách CLI được gọi

Mỗi request một tiến trình, prompt vào stdin, sự kiện JSON ra stdout:

```
claude --print --output-format stream-json --verbose \
       --tools "" --permission-mode dontAsk \
       --no-session-persistence --strict-mcp-config --safe-mode \
       --system-prompt <guardrail> --model <model> \
       [--json-schema <schema theo từng request>]

# khi request khai server tool web_search/web_fetch:
       --tools "WebSearch,WebFetch" --allowed-tools WebSearch WebFetch
```

- `--tools ""` gỡ toàn bộ tool nội trú: model chỉ được trả lời, không đọc file,
  không chạy lệnh, không ra mạng. Chỉ bước `discover` được mở mạng, và cũng chỉ
  mở đúng WebSearch/WebFetch — không bao giờ có Bash/Read/Edit.
- `--safe-mode` bỏ qua `CLAUDE.md`, skill, plugin, hook, MCP, agent riêng —
  nhưng vẫn giữ đăng nhập.
- `--system-prompt` thay prompt agent của Claude Code, giảm overhead từ ~5000
  xuống ~330 token mỗi request.
- Tiến trình chạy trong `code_proxy/runtime/` (thư mục rỗng) và không được kế
  thừa `ANTHROPIC_BASE_URL` — nếu kế thừa, CLI sẽ gọi ngược vào proxy thành vòng
  lặp vô hạn.

Token trả về theo kiểu OpenAI: `prompt_tokens` gộp cả input mới, cache-write và
cache-read; `prompt_tokens_details.cached_tokens` là phần đọc từ cache.

## Bảo mật

CORS đang mở `*` và mặc định không xác thực, nên **mọi trang web bạn đang mở đều
gọi được** proxy này khi nó chạy. Giữ nó ở loopback. Nếu máy có nhiều người dùng
chung, đặt `LLM_PROXY_API_KEY=<chuỗi bí mật>` rồi phía crawler đặt
`ANTHROPIC_AUTH_TOKEN=<chuỗi đó>`.

Proxy hành động bằng tài khoản Claude của bạn. Dùng subscription cá nhân cho tải
tự động chạy trên server có thể nằm ngoài phạm vi cho phép của gói — kiểm tra
điều khoản trước khi đưa lên production; ở đó `ANTHROPIC_API_KEY` mới là đường
đúng, và nó cũng mở lại vision cho bảng B3.
