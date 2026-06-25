# WBS Sync Service

Service đồng bộ dữ liệu **WBS (Work Breakdown Structure)** từ hệ thống nội bộ lên **LangFlow** — theo **từng Part (bộ phận)** + 1 file tập trung. Phát hiện thay đổi theo lịch (mặc định **6 giờ/lần**) và **chỉ re-upload khi dữ liệu thực sự đổi**.

> Mỗi Part có thể **tùy chỉnh** workcode (sửa input/output/mô tả/...). Nên service sync:
> - **1 file default** (tập trung, lấy từ API cũ `/api/works/search`).
> - **1 file riêng cho mỗi Part** (lấy từ `/api/work-profiles?departmentName=<tên Part>`).
>
> Tổng số file đẩy lên = **số Part + 1**.

| Giai đoạn | Việc |
|---|---|
| 🔎 **Fetch** | Lấy list Part (`/api/departments`) → với mỗi Part + default, fetch workcode (phân trang). |
| 🧮 **Detect** | Mỗi target: chuẩn hóa slim + băm **SHA-256**, so với bản newest của target đó. |
| 🚀 **Sync** | Target nào đổi: xóa file cũ trên LangFlow → upload file mới (Files API **v2**). |
| 🧹 **Cleanup** | Part bị xóa khỏi `/api/departments` → tự xóa file tương ứng trên LangFlow. |

---

## 1. Kiến trúc & luồng xử lý

```
/api/departments ─▶ list[Part] ─┐
/api/works/search ─▶ default ───┤
/api/work-profiles?departmentName=… ─▶ mỗi Part ─┤
                                                 ▼
                            ┌────────────── per target ───────────────┐
                            │  transform → slim                        │
                            │  write TEMP  ─▶ compare SHA-256 vs NEWEST│
                            │     ├─ bằng: xóa temp, skip              │
                            │     └─ khác: promote temp→newest → upload│
                            └──────────────────────────────────────────┘
                                                 ▼
                            data/state.json (per-target) + changelog.jsonl (gắn target)
```

### Vòng đời 1 lần chạy — `run_once()` (lặp trên từng target)

1. **Departments** — `wbs_client.fetch_departments()` → list Part.
2. **Build targets** — `default` + mỗi Part (slug hóa tên Part cho tên file).
3. **Orphan cleanup** — Part nào có trong state mà không còn trong list → `langflow.delete_by_base(...)` + xóa khỏi state.
   - **Guard**: nếu list Part rỗng bất thường (mà state đang có Part) → **bỏ qua** cleanup (tránh xóa sạch khi API glitch).
4. **Loop từng target** (`_sync_one`):
   1. **Fetch** workcode của target (default = `/api/works/search`; Part = `/api/work-profiles?departmentName=<tên gốc>`).
   2. **Transform** → slim dicts.
   3. **Temp** — ghi ra `data/<base>.tmp.json` (temp chỉ để compare).
   4. **Compare** — SHA-256 temp vs newest `data/<base>.json`:
      - **Bằng** → xóa temp, skip (không đụng LangFlow, không ghi changelog).
      - **Khác** (hoặc `--force`) → **promote temp → newest ngay** → tính diff → upload.
   5. **Upload (retry)** — `langflow_client.replace_file(newest, filename)` retry `SYNC_MAX_RETRIES`. Mỗi lần: `GET /api/v2/files` → `DELETE` file trùng tên (+ residue `.tmp`) → `POST /api/v2/files`.
   6. **Changelog + state** — 1 dòng changelog (gắn `target` + `langflow_name`), cập nhật state target.

> Mỗi target độc lập: Part A đổi thì **chỉ Part A re-push**, default và Part khác skip.

### Changelog & retry

`data/changelog.jsonl` — log append-only, **chỉ ghi khi target đổi** (hoặc `--force`/orphan). Mỗi dòng có `target` + `langflow_name` + diff + kết quả upload:

```jsonc
{"ts":"2026-06-25T10:00:00+00:00","target":"part:sales","langflow_name":"wbs_agent_knowledge_sales",
 "status":"success","forced":false,"record_count":42,
 "diff":{"added":1,"removed":0,"updated":1,"unchanged":40,"total_new":42,
         "sample_added":["WBS-043"],"sample_updated":[{"code":"WBS-003","fields":["description"]}]},
 "attempts":1,"max_retries":3,"langflow_file_id":"abc","langflow_path":"USER/abc.json","error":null}
```

- **Retry**: upload fail → thử tối đa `SYNC_MAX_RETRIES` lần, backoff `SYNC_RETRY_BACKOFF * attempt` giây. Hết retry → dòng `failed`. Vì newest đã advance, **tick sau không tự retry** → re-push bằng `run-once --force`.
- **Changelog per Part**: default target + sự kiện xóa Part (orphan) ghi ở `data/changelog.jsonl` (root); mỗi Part có changelog riêng ở `data/<slug>/changelog.jsonl`.
  ```bash
  docker compose exec wbs-sync cat /app/data/sales/changelog.jsonl   # changelog Part Sales
  ```

---

## 2. Format dữ liệu

### Record gốc — từ WBS API (`content[]`)

```jsonc
{
  "id": "...", "name": "...", "code": "...", "description": "...",
  "input": "...", "output": "...", "task": "...",
  "workCategory": { "id": "...", "name": "..." },
  "job":          { "id": "...", "name": "..." },
  "createdBy": "...", "updatedBy": "...", "createdAt": "...", "updatedAt": "..."
}
```

### Record slim — đẩy lên LangFlow (giữ cho mọi target)

`workCategory` / `job` làm phẳng thành chuỗi `name`; bỏ `id` + metadata.

```jsonc
{ "name":"...", "code":"...", "description":"...", "input":"...", "output":"...",
  "task":"...", "workCategory":"<name>", "job":"<name>" }
```

Mỗi file = list các record slim này.

### Tên file / slug

- Base name (config `LANGFLOW_FILE_NAME`, mặc định `wbs_agent_knowledge`).
- **Default**: `<base>.json` → LangFlow lưu tên `<base>`.
- **Part**: `<base>_<slug>.json` với `slug = slugify(tên Part)` — lowercase, ký tự không phải `[a-z0-9]` (dấu cách, `/`, `&`, …) thành `_`, trim.
  - `"R&D / Engineering"` → `r_d_engineering` → file `wbs_agent_knowledge_r_d_engineering.json`.
  - `"Sales & Marketing"` → `wbs_agent_knowledge_sales_marketing.json`.
  - Va chạm slug (2 Part ra cùng slug) → thêm `_<i>` (`qa`, `qa_2`).
- `departmentName` khi gọi API dùng **tên gốc** (requests tự URL-encode).

---

## 3. Phát hiện thay đổi (hash, per target)

Mỗi target có `last_hash` riêng trong `state.json`. Quy tắc deterministic: sort list theo `code` + `json.dumps(sort_keys=True, ensure_ascii=False)` → SHA-256. Cùng data → cùng hash (kể cả khi API đổi thứ tự). Khác hash = có đổi → push.

---

## 4. LangFlow Files API v2

Auth `x-api-key`. File v2 theo `user_id`. Upload `<base>.json` → LangFlow lưu tên `<base>` (bỏ extension).

| Bước | Endpoint |
|---|---|
| List | `GET /api/v2/files` |
| Delete | `DELETE /api/v2/files/{file_id}` |
| Upload | `POST /api/v2/files` (multipart `files={"file": ("<base>.json", bytes, "application/json")}`) |

`replace_file(path, filename)` = `delete_by_base(stem)` (xóa mọi file tên == `<base>` hoặc `<base>.tmp` residue) rồi `upload`. `delete_by_base` cũng dùng cho orphan cleanup.

Nguồn: [Files endpoints – Langflow Docs](https://docs.langflow.org/api-files).

---

## 5. Cấu hình

Toàn bộ qua biến môi trường (xem `.env.example`):

```dotenv
# --- WBS API ---
WBS_BASE_URL=http://ip:port
WBS_API_KEY=your_wbs_api_key
WBS_PAGE_SIZE=500
WBS_DEPARTMENTS_PATH=/api/departments       # API 1: list Part
WBS_WORKS_PATH=/api/works/search            # target default
WBS_WORK_PROFILES_PATH=/api/work-profiles   # API 2: theo Part

# --- LangFlow ---
LANGFLOW_BASE_URL=http://langflow-ip:port
LANGFLOW_API_KEY=your_langflow_api_key
LANGFLOW_FILE_NAME=wbs_agent_knowledge      # base name

# --- Service ---
SYNC_INTERVAL_HOURS=6
SYNC_RUN_ON_START=true
SYNC_DEFAULT_ENABLED=true                   # cũng push file tập trung
SYNC_MAX_RETRIES=3
SYNC_RETRY_BACKOFF=5
STATE_DIR=./data
LOG_LEVEL=INFO
HTTP_TIMEOUT=30
```

---

## 6. Cài đặt & chạy

### A. Docker Compose (khuyến nghị)

```bash
cp .env.example .env        # điền key/url
docker compose up -d --build
docker compose logs -f
# ép sync ngay:
docker compose exec wbs-sync python -m wbs_sync run-once --force
```

`./data` mount ra host. Bố cục:

```
data/
├── state.json                       # state per-target (dict)
├── changelog.jsonl                  # default target + sự kiện xóa Part
├── wbs_agent_knowledge.json         # file default (newest)
├── sales/                           # folder từng Part (tên = slug)
│   ├── wbs_agent_knowledge_sales.json
│   └── changelog.jsonl              # changelog riêng của Part này
└── it_ops/                          # "IT & Ops" → slug it_ops
    ├── wbs_agent_knowledge_it_ops.json
    └── changelog.jsonl
```

> File temp (`*.tmp.json`) chỉ tồn tại trong 1 tick rồi dọn (rename thành newest nếu đổi, hoặc xóa). Khi 1 Part bị xóa khỏi `/api/departments`, folder Part đó bị xóa sạch; sự kiện xóa ghi ở root `changelog.jsonl`.

### B. Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m wbs_sync            # scheduler (6h/lần)
python -m wbs_sync run-once   # chạy 1 lần
python -m wbs_sync run-once --force
pytest                        # test
```

---

## 7. Cấu trúc dự án

```
src/wbs_sync/
  config.py          settings (.env): base name, API paths, sync_*
  models.py          WorkCode, WorkCodeSlim, Department, TargetState, State, RunResult
  naming.py          slugify + assign_slugs (xử lý &, /, dấu cách + va chạm)
  transformer.py     full → slim
  wbs_client.py      fetch_departments / fetch_works / fetch_work_profiles (phân trang)
  change_detector.py serialize + SHA-256 + compute_diff
  langflow_client.py list / delete_by_base / upload_file / replace_file (theo filename)
  state.py           load/save state.json (atomic, per-target)
  changelog.py       append/read JSONL
  pipeline.py        SyncTarget + build_targets + _sync_one + orphan cleanup + run_once
  scheduler.py       APScheduler interval
  __main__.py        CLI: serve / run-once [--force]
data/                state.json, changelog.jsonl (root/default) + <base>.json (default)
                     + <slug>/ (mỗi Part: data + changelog riêng)  — gitignored
tests/               pytest + requests-mock
Dockerfile + docker-compose.yml (root)
```

---

## 8. Lỗi & xử lý

| Tình huống | Xử lý |
|---|---|
| Nhiều trang | Lặp `pageNum` tới `len(content) < pageSize` hoặc đủ `totalElements`. |
| Record thiếu field | `None`; không crash, log warning. |
| File trùng tên trên LangFlow | `replace_file` xóa hết file trùng base (+ `.tmp` residue) rồi upload. |
| Upload fail | Retry `SYNC_MAX_RETRIES`; fail → dòng changelog `failed`, newest đã advance → re-push bằng `--force`. |
| Part bị xóa khỏi `/api/departments` | Auto-delete file Part trên LangFlow + khỏi state. |
| List Part rỗng bất thường | Guard: **không** xóa orphan (tránh xóa sạch khi API glitch). |
| Lần đầu (chưa state target) | Coi như đổi → push ngay. |

---

## Liên kết tham khảo
- [Files endpoints – Langflow Docs](https://docs.langflow.org/api-files)
- Kế hoạch per-Part: [plan](docs/) · DEVELOPMENT_PLAN.md
