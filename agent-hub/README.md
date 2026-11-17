# Agent Hub (MVP)

Multi-agent chạy bằng LangGraph, có dashboard xem từng agent đang làm gì và tiến độ.

## Chạy

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Mở http://127.0.0.1:8000 rồi bấm Chạy.

Lưu ý Windows: nếu thư mục dự án nằm ở đường dẫn rất dài, `import xxhash` có thể lỗi
"filename or extension is too long". Khi đó tạo venv ở đường dẫn ngắn (ví dụ `C:\ah-venv`).

Chạy thử không cần mở trình duyệt: bật server rồi `python tests/e2e.py approve` (hoặc `reject`).

## Cấu hình (biến môi trường, đều không bắt buộc)

| Biến | Tác dụng |
|---|---|
| `ANTHROPIC_API_KEY` | Có thì gọi Claude thật; không có thì chạy mô phỏng |
| `CLAUDE_MODEL` | Model dùng, mặc định `claude-sonnet-5-5` |
| `VAULT_DIR` | Thư mục Obsidian vault. Đọc `SOP/hoi-cho.md`, `SOP/tu-cam.txt`; ghi nhật ký vào `Runs/` |
| `N8N_WEBHOOK_URL` | Webhook n8n để Publisher gọi; trống thì bỏ qua |

## Luồng

Supervisor, Researcher, Writer, Video editor, Reviewer. Reviewer không đạt thì quay lại Writer
(tối đa 2 lần). Sau đó dừng chờ người duyệt (`interrupt`), duyệt xong Publisher mới gọi n8n
và ghi nhật ký vào vault.

Chưa làm thật: bước Video editor (đang mô phỏng, chỗ nối pipeline dựng video nằm trong `app/graph.py`, hàm `video`).
