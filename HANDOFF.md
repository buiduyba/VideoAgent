# HANDOFF: vidagents (viết 17/11/2026, từ phiên Claude Code trên laptop)

Dùng file này để Claude trên PC làm tiếp mà không phải hỏi lại từ đầu. Đọc kèm `README.md` (có bảng "Trạng thái thật") và `docs/KIEN-TRUC.md`.

## Dự án là gì
Khung multi-agent (LangGraph) để nhận, dựng và đăng video: 1 điều phối + 4 agent (script, footage, edit, critic), 10 dạng video là cấu hình, 7 nguyên tắc nằm trong code. Ban đầu nhắm video hội chợ, nhưng anh chốt: **sẽ có nhiều dạng video, không riêng hội chợ**.

## Đã làm xong (theo phiên 14/11 đến 16/11/2026)
- MVP LangGraph + UI; `pytest` 125 test đạt trên Windows (AI giả, công cụ giả).
- `vidagents demo` chạy trọn luồng bằng LangGraph thật (AI và công cụ giả).
- Dashboard xem job (`vidagents dashboard`, chỉ xem, cổng 8765).
- Đã làm 2 tài liệu (Claude Artifact, riêng tư, nằm trong tài khoản Claude của anh):
  - Tài liệu thiết kế: https://claude.ai/artifact/XVNjRSckesLSM1q5NGYpCV
  - Bộ slide giới thiệu 11 trang: https://claude.ai/artifact/DWeDp1ksyfU8VPB9ykK1Yr
- Đã tạo workspace "hub agent" trên máy cũ (anh yêu cầu ở phiên 14/11).

## Việc đang dở (phiên dừng giữa chừng, **chưa sửa gì** trong vidagents hay n8n)
Mục tiêu: nối vidagents với n8n để đăng video lên Fanpage Facebook. Kế hoạch lúc dừng:
1. Xem n8n của anh: loại node, credential có sẵn (đã đọc 3 workflow, xem bên dưới).
2. Đọc mã vidagents: cổng duyệt, giao kết quả, bot.
3. Thêm **cổng duyệt đăng** và bước gọi n8n (kèm test).
4. Dựng workflow n8n (chưa bật).
5. Cập nhật tài liệu nếu nguyên tắc thay đổi.

Workflow n8n đã có (instance `duy2test.app.n8n.cloud`, cần đăng nhập đúng tài khoản/kết nối n8n trong Claude): "Webhook - Đăng video lên Fanpage Facebook", "Trợ lý đăng bài FB Oxiclean", "Team Ads - Video: Drive, vault, GitHub". Trợ lý đăng bài còn gọi sang instance `taytran10522.app.n8n.cloud` mà Claude không truy cập được.

**Ba điều anh cần chốt trước khi làm bước 3 đến 5:**
1. Video đến n8n bằng cách nào: chép vào thư mục Drive hay dán link?
2. Fanpage cấu hình theo thương hiệu, hay nhập khi duyệt?
3. Caption do Claude soạn hay người giao gõ?

Ba việc cần quyết ở slide cuối: nạp credit API, chọn dạng video đầu tiên (đề xuất hội chợ), chốt người duyệt.

## Bảo mật: làm sớm
Workflow "Trợ lý đăng bài FB Oxiclean" có **token bot Telegram nằm thẳng trong node HTTP Request**. Thu hồi token ở BotFather và dùng credential Telegram có sẵn của n8n. (Chưa biết anh đã làm chưa.) Không dán token vào chat hay file.

## Chưa kiểm chứng
- Chưa gọi Claude API thật (có `vidagents demo --live --max-usd 1`).
- Bot Telegram và watchdog chưa chạy với token thật.
- Tiếp tục job sau khi tắt máy (checkpoint SQLite) chưa thử.
- Chế độ A, C, S chưa có lệnh (chờ skill). Chế độ B chép từ skill dựng video nhưng chưa chạy từ khung này.

## Dựng lại trên PC
Thư mục này chưa có git trước đây. Sau khi anh đẩy lên GitHub, trên PC:
```
git clone <link repo>
cd vidagents
py -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy config.example.json config.json
pytest
vidagents demo
```
- `config.json` bị `.gitignore` (không lên GitHub): tạo từ `config.example.json`, sửa đường dẫn và ID Telegram.
- Khóa chỉ đặt qua biến môi trường: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`. Không ghi vào file.
- Nếu đường dẫn dự án dài, tạo `.venv` ở đường dẫn ngắn (Windows báo "filename or extension is too long").
- `.claude\launch.json` đang trỏ tới máy cũ (`C:\Users\bdduy\.vidagents-venv`, `C:\Users\bdduy\vidagents-dash-demo`): sửa lại trên PC, hoặc tạo lại bằng `vidagents demo --workdir <thư mục>` rồi `vidagents dashboard --demo-dir <thư mục> --port 8765`.

## Lời nhắn cho Claude trên PC
Đọc README (bảng "Trạng thái thật") và `docs/KIEN-TRUC.md` trước. Đừng khẳng định phần "chưa kiểm chứng" là chạy được. Hỏi anh ba câu ở mục "Việc đang dở" rồi làm tiếp từ bước 3. Việc có hậu quả (duyệt, hủy, đăng) chỉ đi qua Telegram, đừng thêm nút duyệt vào dashboard.
