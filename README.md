# WBS Sync Service

Service đồng bộ dữ liệu **WBS (Work Breakdown Structure)** từ hệ thống nội bộ lên **LangFlow**.
Phát hiện thay đổi theo lịch (mặc định **6 giờ/lần**) và **chỉ re-upload khi dữ liệu thực sự đổi**.

> Mục đích: agent chạy trên LangFlow cần một file `wbs.json` luôn cập nhật nhưng **gọn nhẹ** — chỉ giữ các trường agent dùng tới, bỏ metadata thừa (`id`, `createdBy`, `updatedAt`, ...). Service này đảm bảo file đó luôn đồng bộ mà không spam API LangFlow mỗi chu kỳ.

| Giai đoạn | Việc |
|---|---|
| 🔎 **Fetch** | Lấy danh sách workcode qua WBS API (có phân trang). |
| 🧮 **Detect** | Chuẩn hóa về **slim format** + băm **SHA-256** để biết có thay đổi hay không. |
| 🚀 **Sync** | Nếu đổi: **xóa file cũ** trên LangFlow → **upload file mới** (LangFlow Files API **v2**). |

---

## 1. Kiến trúc & luồng xử lý

```
┌───────────────┐  fetch (phân trang)   ┌───────────────┐
│   WBS API     │ ─────────────────────▶│   wbs_client  │
│ /works/search │                        └──────┬────────┘
└───────────────┘                               │ list[WorkCode]  (đầy đủ)
                                                ▼
                          ┌──────────────────────────────────┐
                          │           transformer            │  lọc field
                          └──────┬───────────────────┬───────┘
                                 │                   │
                  list[WorkCodeSlim]        serialize chuẩn + SHA-256
                                 │                   │
                                 ▼                   ▼
                          ┌──────────────┐   ┌───────────────┐
                          │  data/wbs.json│   │ change_detector│
                          └──────────────┘   └──────┬────────┘
                                                   │ hash mới vs hash cũ
                                            ┌──────▼────────┐
                                            │  changed?     │
                                            └───┬───────┬───┘
                                       KHÔNG │   │ CÓ   │
                                   (log, skip)│   │      │
                                              │   ▼      │
                                              │ ┌──────────────────┐
                                              │ │  langflow_client │ delete old → upload new
                                              │ │  (API v2)        │
                                              │ └──────────────────┘
                                              ▼
                                     data/state.json  (cập nhật last_hash)
```

### Vòng đời 1 lần chạy — `run_once()`

1. **Fetch** — `wbs_client.fetch_all()` duyệt các trang (`pageNum` tăng dần, `pageSize=500`) tới khi hết dữ liệu.
2. **Transform** — `transformer.to_slim(record)` map từng record về slim format.
3. **Temp** — ghi dữ liệu mới ra file **candidate** `data/wbs.tmp.json` (temp).
4. **Compare** — băm SHA-256 nội dung temp rồi so với hash của file **newest** `data/wbs.json` (đọc lại từ disk — compare "trước/sau"):
   - **Bằng** → không đổi → **xóa temp**, kết thúc (không đụng LangFlow, không ghi changelog).
   - **Khác** (hoặc `--force`) → có đổi → **promote temp → newest ngay** (newest luôn giữ data mới nhất) → tính **diff** (thêm/sửa/xóa) → upload.
5. **Upload (retry)** — `langflow_client.replace_file(newest)` với **retry** (`SYNC_MAX_RETRIES`, backoff tuyến tính). Mỗi lần: `GET /api/v2/files` → `DELETE` hết file trùng tên (+ residue `.tmp`) → `POST /api/v2/files` upload dưới tên `wbs.json`.
6. **Changelog + state** — ghi 1 dòng changelog (`success`/`failed`), cập nhật state: `last_hash` luôn advance theo newest, `last_synced_at` chỉ advance khi upload OK, `last_status` phản ánh kết quả.

> File temp chỉ tồn tại để compare, xong là dọn → chỉ có **một file data** lúc nghỉ (`wbs.json`).
> Nếu upload **fail** sau khi hết retry: newest **đã là bản mới** (theo thiết kế), nên tick kế tiếp thấy "không đổi" sẽ **không** tự upload lại → muốn đẩy lại thì `run-once --force`.

> Chỉ có **một file data** tồn tại lúc nghỉ (`wbs.json` = newest). File temp chỉ sống trong thời gian 1 tick rồi luôn bị dọn (rename thành newest nếu đổi, hoặc unlink nếu không đổi / lỗi).

> Việc tách `run_once()` (chạy 1 lần) và scheduler (lặp theo lịch) giúp cùng code chạy trong-process (APScheduler) hoặc qua `cron` gọi one-shot.

### Changelog & retry

`data/changelog.jsonl` là log **append-only** (mỗi dòng 1 JSON), chỉ ghi khi có **thay đổi thật** (hoặc `--force`). Mỗi dòng ghi đủ: *cái gì đổi* (diff thêm/sửa/xóa + sample 5 mã), *upload thành công hay không*, *số lần thử*, *error* nếu fail.

```jsonc
{"ts":"2026-06-24T10:00:00+00:00","status":"success","forced":false,"record_count":120,
 "diff":{"added":2,"removed":0,"updated":1,"unchanged":117,"total_new":120,
         "sample_added":["WBS-121","WBS-122"],"sample_removed":[],
         "sample_updated":[{"code":"WBS-003","fields":["description"]}]},
 "attempts":1,"max_retries":3,"langflow_file_id":"abc","langflow_path":"USER/abc.json","error":null}
```

- **Retry**: upload fail → thử lại tối đa `SYNC_MAX_RETRIES` lần, mỗi lần cách `SYNC_RETRY_BACKOFF * attempt` giây. Hết retry → ghi dòng `failed`. Vì newest đã advance ngay khi phát hiện đổi, **tick sau sẽ không tự retry** (thấy "không đổi") — muốn re-push thì `run-once --force`.
- **Mount trong Docker**: `changelog.jsonl` nằm trong `data/`, đã được mount `./data:/app/data` → persists ra host, đọc/parse bằng `jq` thoải mái.
  ```bash
  docker compose exec wbs-sync tail -n 20 /app/data/changelog.jsonl
  docker compose exec wbs-sync sh -c 'cat /app/data/changelog.jsonl | jq -c "select(.status==\"failed\")"'
  ```

---

## 2. Format dữ liệu

### Record gốc — từ WBS API (`response.json()['content']`)

```jsonc
{
  "id": "...", "name": "...", "code": "...", "description": "...",
  "input": "...", "output": "...", "task": "...",
  "workCategory": { "id": "...", "name": "..." },
  "job":          { "id": "...", "name": "..." },
  "createdBy": "...", "updatedBy": "...",
  "createdAt": "...", "updatedAt": "..."
}
```

### Record slim — chuẩn chung + đẩy lên LangFlow

Chỉ giữ đúng trường agent cần. `workCategory` / `job` được **làm phẳng thành chuỗi tên** (lấy `name`).

```jsonc
{
  "name": "...", "code": "...", "description": "...",
  "input": "...", "output": "...", "task": "...",
  "workCategory": "<name>",     // tên dạng chuỗi, bỏ id
  "job":          "<name>"      // tên dạng chuỗi, bỏ id
}
```

> Toàn bộ file đẩy lên là một **list** các record slim này: `data/wbs.json` = `[ {...}, {...}, ... ]`.

**Quyết định mở (có thể đổi):**
- `workCategory`/`job` làm phẳng thành **string** (khuyến nghị, gọn cho agent). Muốn giữ dạng object thì giữ `{"name": "..."}`.
- Bỏ `id` khỏi slim record (agent không cần). Nếu muốn dùng `id` làm định danh nội bộ cho diff thì thêm vào **chỉ ở hash, không đẩy lên LangFlow**.

---

## 3. Cơ chế phát hiện thay đổi (hash vs. compare toàn file)

Hai hướng:

| Cách | Ưu | Nhược |
|---|---|---|
| **Compare toàn file** (lưu JSON cũ, diff) | Dễ hiểu, biết được *chính xác* field nào đổi | Phải lưu bản cũ nguyên vẹn; diff tốn công |
| **Hash (SHA-256) — KHUYẾN NGHỊ** | Lưu state siêu nhỏ (chỉ 1 chuỗi hash), so sánh O(1), xác định "có/không đổi" ngay | Không cho biết *chi tiết* gì đổi (nhưng service này không cần) |

**Mục tiêu thật sự của bước detect** chỉ là trả lời: *"có thay đổi không?"* để **tránh gọi API LangFlow thừa**. Vì file mới luôn được tái tạo từ data tươi, ta không cần giữ bản cũ — chỉ cần biết hash có trùng không.

**Bảo đảm tính tất định (deterministic) khi hash:**
- **Sort list** theo `code` (fallback `name`) → API trả thứ tự khác cũng không sinh "đổi giả".
- `json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.
- Luôn dùng cùng bộ cờ serialize → cùng dữ liệu luôn ra cùng 1 hash.

Lưu `last_hash` (và vài metadata: `last_synced_at`, `langflow_file_id`, `record_count`) trong `data/state.json`.

---

## 4. LangFlow Files API v2 (đã đối chiếu docs chính thức)

Auth: header `x-api-key: <LANGFLOW_API_KEY>`. File v2 theo **`user_id`** (không gắn `flow_id`), được track trong DB, hỗ trợ bulk. **Lưu ý:** v2 **không** hỗ trợ file ảnh — nhưng ta đẩy `.json` nên OK.

| Bước | Method + Endpoint | Payload / Response |
|---|---|---|
| **List** | `GET /api/v2/files` | resp: list `{ id, name, path, size, provider }` |
| **Delete 1** | `DELETE /api/v2/files/{file_id}` | — |
| **Delete all** | `DELETE /api/v2/files` | (dự phòng, cẩn trọng) |
| **Upload** | `POST /api/v2/files` | multipart `files={"file": ("<base>.json", bytes, "application/json")}` → resp `{ id, name, path, size, provider }` |

> **Quan trọng về tên:** v2 trả `name` **không kèm extension**. Upload `wbs.json` → `name == "wbs"`. Nên chọn **một base name cố định** (mặc định `wbs`) để list & tìm file cũ cho khớp.

Nguồn: [Files endpoints – Langflow Docs](https://docs.langflow.org/api-files), [Manage files](https://docs.langflow.org/concepts-file-management).

---

## 5. Cấu hình

Toàn bộ qua biến môi trường (load từ `.env`). Mẫu nội dung `.env`:

```dotenv
# --- WBS API (nguồn dữ liệu) ---
WBS_BASE_URL=http://ip:port
WBS_API_KEY=your_wbs_api_key
WBS_PAGE_SIZE=500

# --- LangFlow API (đích) ---
LANGFLOW_BASE_URL=http://langflow-ip:port
LANGFLOW_API_KEY=your_langflow_api_key
LANGFLOW_FILE_NAME=wbs            # base name (không kèm .json)

# --- Service ---
SYNC_INTERVAL_HOURS=6             # chu kỳ chạy
SYNC_RUN_ON_START=true            # chạy ngay 1 lần khi khởi động
SYNC_MAX_RETRIES=3                # số lần thử upload khi fail
SYNC_RETRY_BACKOFF=5              # backoff tuyến tính (giây) giữa các lần thử
STATE_DIR=./data                  # chứa state.json, wbs.json (newest), changelog.jsonl
LOG_LEVEL=INFO
HTTP_TIMEOUT=30
```

---

## 6. Cài đặt & chạy

### A. Deploy bằng Docker Compose (khuyến nghị)

File `docker-compose.yml` nằm ngay ở root. Container chạy APScheduler (giữ tiến trình sống, `restart: unless-stopped`), mount `./data` để persist `state.json` + `wbs.json`.

```bash
# 1) Cấu hình
cp .env.example .env        # điền WBS_BASE_URL, WBS_API_KEY, LANGFLOW_BASE_URL, LANGFLOW_API_KEY

# 2) Khởi động (build image + chạy nền)
docker compose up -d --build

# Xem log / trạng thái
docker compose logs -f
docker compose ps

# Dừng / rebuild
docker compose down
```

> `STATE_DIR` bị ép thành `/app/data` trong compose (không đụng tới giá trị trong `.env`) — ánh xạ với `./data` trên host qua volume.

Chạy **một lần ngay** (debug, ép sync) mà không chờ chu kỳ:

```bash
docker compose exec wbs-sync python -m wbs_sync run-once --force
```

### B. Chạy local (dev/debug)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # hoặc: pip install -r requirements-dev.txt && pip install -e .
cp .env.example .env        # điền cấu hình

# Chạy theo lịch (mặc định 6h/lần, APScheduler)
python -m wbs_sync

# Chạy 1 lần duy nhất rồi thoát
python -m wbs_sync run-once

# Ép sync kể cả khi hash không đổi (debug / đẩy lại lần đầu)
python -m wbs_sync run-once --force

# Chạy test
pytest
```

> Phương án deploy khác (không cần tiến trình treo): `cron` gọi `python -m wbs_sync run-once` mỗi 6 giờ.

---

## 7. Cấu trúc dự án

```
wbs-sync-service/
├── README.md
├── docs/
│   └── DEVELOPMENT_PLAN.md      # kế hoạch triển khai theo phase
├── .env.example                 # mẫu biến môi trường
├── requirements.txt             # runtime deps
├── requirements-dev.txt         # + deps test
├── pyproject.toml               # build/test config, extra [dev]
├── Dockerfile                   # image python:3.12-slim
├── docker-compose.yml           # deploy 1 lệnh ở root
├── .dockerignore
├── src/wbs_sync/
│   ├── __init__.py
│   ├── __main__.py              # entrypoint CLI: `python -m wbs_sync [run-once]`
│   ├── config.py                # load .env + validate (pydantic-settings)
│   ├── models.py                # WorkCode (đầy đủ) + WorkCodeSlim + State
│   ├── transformer.py           # map full → slim (workCategory/job → string)
│   ├── wbs_client.py            # fetch WBS (phân trang)
│   ├── change_detector.py       # serialize chuẩn + SHA-256 + so state
│   ├── state.py                 # đọc/ghi data/state.json (atomic)
│   ├── langflow_client.py       # list / delete / upload (API v2)
│   ├── pipeline.py              # run_once(): ráp fetch→detect→sync
│   └── scheduler.py             # APScheduler: gọi run_once() theo chu kỳ
├── data/                        # sinh runtime, .gitignore (mount volume trong Docker)
│   ├── state.json
│   └── wbs.json
└── tests/                       # pytest + requests-mock (33 test)
```

---

## 8. Lỗi & xử lý

| Tình huống | Xử lý |
|---|---|
| WBS API trả nhiều trang | Lặp `pageNum` tới khi `len(content) < pageSize` hoặc đủ `totalElements`. |
| Một record thiếu field | Thiếu → gán `None`/`""`; không crash. Log warning. |
| List LangFlow có nhiều file trùng tên | Xóa **hết** các id trùng tên rồi mới upload bản mới. |
| Upload OK nhưng xóa cũ fail | Vẫn coi sync thành công; lần sau sẽ dọn dẹp file thừa. |
| Delete fail / upload fail | Retry đơn giản (n lần); lỗi thì log + giữ hash cũ để chạy lại lần sau. |
| Lần chạy đầu (chưa có state) | Coi như "có thay đổi" → push ngay lần đầu. |

---

## Liên kết tham khảo
- [Files endpoints – Langflow Docs](https://docs.langflow.org/api-files)
- [Get started with the Langflow API](https://docs.langflow.org/api-reference-api-examples)
- Kế hoạch triển khai chi tiết: [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md)
