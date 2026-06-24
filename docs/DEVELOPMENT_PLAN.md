# Kế hoạch triển khai — WBS Sync Service

Tài liệu này chia việc ra **8 phase**, ưu tiên chạy được end-to-end sớm rồi mới bổ sung tính năng. Mỗi phase có **mục tiêu / công việc / kết quả / tiêu chí nghiệm thu**.

> Nguyên tắc: **vertical slice trước** — phase 1→4 phải chạy được toàn bộ pipeline (fetch → detect → sync) bằng code thô nhất, sau đó mới tinh chỉnh (config, test, deploy).

---

## Tổng quan các phase

| # | Phase | Mục tiêu cốt lõi | Phụ thuộc |
|---|---|---|---|
| 0 | Khởi tạo dự án | skeleton, deps, config, `.env` | — |
| 1 | WBS Client | lấy hết workcode (phân trang) | 0 |
| 2 | Transformer + Change Detector + State | slim format + hash + biết "đổi/không đổi" | 1 |
| 3 | LangFlow Client | list / delete / upload file (API v2) | 0 |
| 4 | Pipeline `run_once()` | ráp fetch → detect → sync; chạy được E2E | 1,2,3 |
| 5 | Scheduler + CLI | chạy theo lịch 6h + lệnh one-shot | 4 |
| 6 | Test | unit + integration (mock HTTP) | 1–4 |
| 7 | Đóng gói & Deploy | `requirements.txt`, hướng dẫn run, cron/systemd | 5 |

---

## Phase 0 — Khởi tạo dự án

**Mục tiêu:** có skeleton chạy được `import wbs_sync`, load được config.

**Công việc:**
- Tạo cấu trúc thư mục (xem README mục 7).
- `requirements.txt`:
  ```text
  requests>=2.31
  APScheduler>=3.10
  python-dotenv>=1.0
  pydantic>=2.5
  pydantic-settings>=2.1
  ```
- `src/wbs_sync/config.py`: dùng `pydantic-settings` load từ `.env`, validate (URL hợp lệ, key không rỗng, `SYNC_INTERVAL_HOURS>0`).
- Tạo `.env.example` (copy từ README mục 5).
- Thêm `data/` vào `.gitignore` (nếu chưa).

**Kết quả:** chạy `python -c "from wbs_sync.config import get_settings; print(get_settings())"` in ra settings đúng.

**Tiêu chí nghiệm thu:** settings thiếu key bắt buộc → báo lỗi rõ ràng; `.env.example` có đủ biến.

---

## Phase 1 — WBS Client

**Mục tiêu:** `fetch_all()` trả về `list[WorkCode]` đầy đủ.

**Công việc:**
- `models.py`: định nghĩa `WorkCode` (pydantic) theo đúng các field gốc (`id, name, code, description, input, output, task, workCategory{id,name}, job{id,name}, createdBy, updatedBy, createdAt, updatedAt`). Cho phép field thiếu (`extra="ignore"` hoặc optional).
- `wbs_client.py`:
  - `fetch_page(page_num) -> dict`: gọi `GET {WBS_BASE_URL}/api/works/search` với header `x-api-key`, params `pageNum`, `pageSize`.
  - `fetch_all() -> list[WorkCode]`: lặp `pageNum=1,2,...` tới khi `len(content) < WBS_PAGE_SIZE` (hoặc đủ `totalElements` nếu API có).
  - Dùng một `requests.Session`, set `timeout=HTTP_TIMEOUT`, `raise_for_status()`.
- **Điểm cần xác nhận từ API thật:** cấu trúc wrapper ngoài `content` — có `totalElements`/`totalPages` không? (Spring pagination thường có). Ưu tiên dùng `totalPages` để dừng loop cho chính xác.

**Kết quả:** `python -m wbs_sync run-once --fetch-only` (flag debug tạm) in ra số record + 3 record đầu.

**Tiêu chí nghiệm thu:** lấy đúng **toàn bộ** record (đếm khớp `totalElements` nếu có); record thiếu field không làm crash.

---

## Phase 2 — Transformer + Change Detector + State

**Mục tiêu:** tạo slim format, tính hash, so với state cũ để biết có đổi.

**Công việc:**
- `transformer.py`:
  - `to_slim(record: WorkCode) -> WorkCodeSlim`: giữ `name, code, description, input, output, task`; `workCategory`/`job` → lấy `name` làm chuỗi.
  - `to_slim_list(records) -> list[WorkCodeSlim]`.
- `models.py`: thêm `WorkCodeSlim`.
- `change_detector.py`:
  - `_serialize(slim_list)`: **sort** theo `code` (fallback `name`), `json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.
  - `compute_hash(slim_list) -> str`: SHA-256 hex của chuỗi serialize.
  - `has_changed(new_hash, state) -> bool`: so với `state.last_hash`; trả `True` nếu chưa có state (lần đầu).
- `state.py`:
  - `load() -> State` / `save(state)`: đọc/ghi `data/state.json` dạng:
    ```json
    {
      "last_hash": "sha256...",
      "last_synced_at": "2026-06-24T10:00:00Z",
      "langflow_file_id": "...",
      "langflow_path": "USER_ID/FILE_ID.json",
      "record_count": 123
    }
    ```
  - ghi atomic (ghi file tạm rồi `os.replace`) để chống hỏng state khi crash giữa chừng.

**Kết quả:** chạy transform + hash trên data thật, in hash ra.

**Tiêu chí nghiệm thu:**
- Cùng data chạy 2 lần → **cùng hash** (dù API trả thứ tự khác).
- Đổi 1 trường bất kỳ → hash khác.
- Lần đầu (chưa state) → `has_changed == True`.

---

## Phase 3 — LangFlow Client (API v2)

**Mục tiêu:** `replace_file(path)` thực hiện "xóa cũ + upload mới".

**Công việc:** `langflow_client.py`, base URL `{LANGFLOW_BASE_URL}/api/v2/files`, header `x-api-key`. Các hàm:
- `list_files() -> list[dict]` → `GET /api/v2/files` (parse response → list).
- `delete_file(file_id)` → `DELETE /api/v2/files/{file_id}`.
- `upload_file(path) -> dict` → `POST /api/v2/files`, multipart `files={"file": (f"{LANGFLOW_FILE_NAME}.json", bytes, "application/json")}`. Trả `{id, name, path, size}`.
- `replace_file(path)`:
  1. `list_files()` → lọc item có `name == LANGFLOW_FILE_NAME` (v2 bỏ extension).
  2. `delete_file(id)` cho **tất cả** id khớp (chống file thừa do lần trước lỗi).
  3. `upload_file(path)` → trả metadata mới.
- Mỗi hàm: `timeout`, `raise_for_status()`, log ngắn gọn. Bọc lỗi HTTP thành exception riêng (`LangFlowError`).

**Kết quả:** test tay — upload 1 file `wbs.json` rỗng, chạy `replace_file` lần 2 → trên LangFlow chỉ còn 1 file tên `wbs`.

**Tiêu chí nghiệm thu:**
- Không có file cũ → vẫn upload thành công.
- Có nhiều file trùng tên → xóa hết, chỉ còn 1 file mới.
- Lỗi 401/403 → log rõ "sai API key".

---

## Phase 4 — Pipeline `run_once()`

**Mục tiêu:** ráp thành 1 hàm chạy end-to-end.

**Công việc:** `pipeline.py`:
```python
def run_once(force: bool = False) -> SyncResult:
    records = wbs_client.fetch_all()
    slim = transformer.to_slim_list(records)
    new_hash = change_detector.compute_hash(slim)
    state = state_store.load()

    if not force and not change_detector.has_changed(new_hash, state):
        log.info("no change; skip sync")
        return SyncResult(changed=False, record_count=len(slim))

    # có đổi (hoặc force)
    path = data_dir / f"{cfg.langflow_file_name}.json"
    write_json_atomic(path, slim, ensure_ascii=False, indent=2)
    meta = langflow_client.replace_file(path)
    state_store.save(State(
        last_hash=new_hash,
        last_synced_at=now_iso(),
        langflow_file_id=meta["id"],
        langflow_path=meta["path"],
        record_count=len(slim),
    ))
    return SyncResult(changed=True, record_count=len(slim), file_id=meta["id"])
```
- Ghi `wbs.json` cục bộ bằng cách atomic (dùng cho upload + audit).
- Bắt lỗi theo từng giai đoạn: **fetch fail** → không đụng state; **sync fail** → **không** cập nhật `last_hash` (để lần sau thử lại).

**Kết quả:** `python -m wbs_sync run-once` chạy trọn vẹn lần đầu.

**Tiêu chí nghiệm thu:**
- Lần 1 (chưa state) → push lên LangFlow, có `state.json`.
- Lần 2 ngay sau (data không đổi) → log "no change", **không** gọi API LangFlow.
- Đổi data thật → push lại, `last_hash` cập nhật.

---

## Phase 5 — Scheduler + CLI

**Mục tiêu:** chạy nền theo lịch; hỗ trợ lệnh one-shot cho cron.

**Công việc:**
- `__main__.py`: parse argparse:
  - `python -m wbs_sync` → scheduler.
  - `python -m wbs_sync run-once [--force]` → chạy 1 lần rồi thoát.
- `scheduler.py`: APScheduler `BlockingScheduler`, `add_job(run_once, "interval", hours=SYNC_INTERVAL_HOURS)`. Nếu `SYNC_RUN_ON_START` → gọi `run_once()` ngay khi khởi động. Bắt `SIGTERM`/`SIGINT` để thoát sạch.

**Kết quả:** để `SYNC_INTERVAL_HOURS` nhỏ (vd 1 phút) test lặp, log thấy chu kỳ chạy đúng.

**Tiêu chí nghiệm thu:** scheduler chạy đúng chu kỳ; `run-once` chạy xong tự thoát; Ctrl+C thoát sạch không treo.

---

## Phase 6 — Test

**Mục tiêu:** đảm bảo logic cốt lõi ổn khi refactor/sửa API.

**Công việc:** dùng `pytest` + mock HTTP (`responses` hoặc `requests_mock`):
- **Unit:**
  - `transformer`: map đúng field; thiếu field → `None`/`""`.
  - `change_detector`: determinism (2 lần = cùng hash); đổi field → hash khác; sort đúng.
  - `state`: load/save round-trip; ghi atomic.
- **Integration (mock):**
  - `wbs_client.fetch_all`: 2 trang → gộp đủ record.
  - `langflow_client.replace_file`: có sẵn 2 file trùng tên → xóa cả 2 + upload 1.
  - `run_once`: data không đổi → **không** gọi LangFlow; data đổi → gọi đúng sequence list→delete→upload.

**Tiêu chí nghiệm thu:** `pytest` xanh; coverage ≥ ~80% trên các module `transformer`, `change_detector`, `state`, `pipeline`.

---

## Phase 7 — Đóng gói & Deploy ✅

**Mục tiêu:** deploy bằng **Docker Compose** ngay ở root (theo chốt của user).

**Đã làm:**
- `Dockerfile` (`python:3.12-slim`, `PYTHONPATH=/app/src`, CMD `python -m wbs_sync`).
- `docker-compose.yml` ở root: `build: .`, `restart: unless-stopped`, `env_file: .env`, ép `STATE_DIR=/app/data`, mount `./data:/app/data`.
- `.dockerignore` (bỏ `.git`, `tests`, `.env`, `data`... khỏi image).
- `pyproject.toml` có extra `[dev]` → `pip install -e ".[dev]"`.

**Chạy:**
```bash
cp .env.example .env          # điền key/url
docker compose up -d --build
docker compose logs -f
# ép sync ngay:
docker compose exec wbs-sync python -m wbs_sync run-once --force
```

**Đã verify:** `docker build` thành công, container chạy được `python -m wbs_sync`, `docker compose config` hợp lệ, lỗi thiếu config in ra thông báo sạch (exit 2).

**Còn có thể bổ sung sau (optional):** logging ra file (rotating), healthcheck/heartbeat, Docker image scanning.

---

## Rủi ro & lưu ý

| Rủi ro | Giảm thiểu |
|---|---|
| WBS API pagination: cấu trúc `totalElements` chưa rõ | Phase 1 xác nhận với API thật; fallback dừng theo `len(content) < pageSize`. |
| State hỏng khi crash giữa ghi | Ghi atomic (`os.replace`) trong `state.py`. |
| Upload OK nhưng delete fail → file thừa trên LangFlow | `replace_file` luôn **list + xóa hết** file trùng tên trước khi upload. |
| Sync fail → hash bị cập nhật nhầm → mất thay đổi | **Chỉ** ghi `last_hash` **sau khi** upload thành công. |
| Rate limit / timeout LangFlow | Retry đơn giản (n lần, backoff); `HTTP_TIMEOUT` cấu hình được. |
| Ký tự Unicode (tiếng Việt) | `ensure_ascii=False` xuyên suốt serialize + ghi file. |
| Lần đầu chưa có state | Coi là "có thay đổi", push ngay. |

---

## Quyết định đã chốt

1. **`workCategory`/`job`** → **string** (lấy `name`, bỏ `id`). ✅
2. **`id`** → **bỏ** khỏi record slim. ✅
3. **Scheduler** → **APScheduler** trong-process (container Docker giữ tiến trình sống). ✅
4. **`SYNC_INTERVAL_HOURS`** → **6** (mặc định, đổi qua env). ✅
5. **Deploy** → **Docker Compose** ở root. ✅
