# LangFlow custom components

Các component dán vào node **Custom Component** trong LangFlow để đọc file WBS
knowledge mà service đã sync lên. Tất cả đều đọc theo **tên file** (tra DB file
management), không theo **path** — vì service re-push = xóa cũ + up mới, path bị
hash đổi mỗi lần → tham chiếu theo path sẽ gãy sau mỗi lần sync; theo tên thì ổn.

| File | Component | Khi nào dùng |
|---|---|---|
| `read_via_partname.py` | **Read WBS Via Part Name** | Nhập tên Part → tự ghép `wbs_agent_knowledge_<slug>` → đọc. Có cả **Structured Content** (DataFrame, mỗi record 1 dòng) + JSON + Raw. **Khuyên dùng.** |
| `read_from_filename.py` | Read File By Name | Nhập đúng tên file → đọc ra Content (text) + Data. Không có Structured Content. |
| `file_component_default.py` | File (mặc định) | Tham chiếu: component built-in đọc theo path. Gãy sau mỗi lần sync → lý do 2 component trên ra đời. |

## `read_via_partname` — cách dùng

- **Part Name**: tên bộ phận như khi gọi work-profile (vd `Sales`, `IT & Ops`).
  Đưa tên gốc hay slug đều OK (slugify idempotent). Để trống → đọc file default
  tập trung (`wbs_agent_knowledge`).
- **File Base Name** (advanced): base name service up, mặc định
  `wbs_agent_knowledge` (trùng `LANGFLOW_FILE_NAME`).
- Outputs (giống File component cho 1 file JSON):
  - **Structured Content** — DataFrame, mỗi record 1 dòng (cho các node xử lý table).
  - **JSON Content** — Data wrap JSON đã parse.
  - **Raw Content** — Message text JSON (cho Prompt/Agent).

## Hợp đồng slug (QUAN TRỌNG — dễ vỡ)

Tên file do component tính = `f"{base}_{slugify(part_name)}"`. Hàm `_slugify`
trong `read_via_partname.py` phải **giống hệt** `slugify` ở
[`src/wbs_sync/naming.py`](../src/wbs_sync/naming.py):

> lowercase → gộp các run ký tự không phải `[a-z0-9]` thành 1 `_` → trim `_` ở 2 đầu.

Nếu đổi rule slug ở service, phải đổi `_slugify` trong component cho khớp, nếu
không tên tính ra sẽ lệch với tên đã upload → không tìm thấy file.

Đã verify đồng bộ 2 phía (18 test case khớp byte-byte).
