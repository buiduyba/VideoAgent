# vidagents — khung multi-agent dựng video (LangGraph) cho team

1 điều phối + 4 agent (script · footage · edit · critic), 10 dạng video là cấu hình, 7 nguyên tắc nằm trong code.
**Skill để trống (bản tạm), thả vào sau**: mỗi agent có `agents/<tên>/SKILL.md` + `tools.json` + `lessons.jsonl` riêng.

## Trạng thái thật (đọc trước)
| Phần | Đã kiểm chứng? |
|---|---|
| Logic các node, định tuyến, cổng duyệt, giới hạn vòng, khóa render, kho bài học, khuôn dữ liệu, chống chèn lệnh | **Có**: 125 test đạt trên Windows (AI giả, công cụ giả). Khóa render đã sửa lỗi bỏ rơi khóa trên Windows (xem `docs/KIEN-TRUC.md` mục 5) |
| Lớp nối LangGraph (`graph.py`), checkpointer SQLite, `interrupt`/`Command` | **Có (14/11/2026)**: `vidagents demo` chạy trọn luồng với LangGraph thật trên Windows + Python 3.12 (AI và công cụ giả). **Chưa** kiểm chứng: tiếp tục sau khi tắt máy |
| Dashboard xem job (`dashboard.py`, `web/index.html`) | **Có** test (15), và đã xem thử trên trình duyệt với checkpoint thật của demo. Chỉ đọc, tự làm mới mỗi 2 giây. Chưa thử với bot + Claude thật chạy song song |
| Gọi Claude API có ép khuôn (`llm.py`) | **Chưa** chạy với key thật. Đã có `vidagents demo --live` (trần chi phí, từ chối nếu thiếu key/giá) để chạy thử, xem mục "Chạy thử với Claude thật" |
| Bot Telegram (`bot.py`), cảnh báo video đứng (`watchdog.py`) | **Chưa** chạy với token thật (logic có test, phần gửi tin chưa) |
| Chi phí AI theo job + trần (`usage.py`) | Logic **có** test. Bảng giá mặc định đã đối chiếu trang giá chính thức ngày 14/11/2026 (opus/sonnet/haiku 5.5); riêng Haiku 5.5 tính theo bậc prompt ≤ 100.000 token. Giá đổi thì phải kiểm lại |
| Đối chiếu giá/%/ngày với brief (`factcheck.py`) | **Có** test. Chỉ đọc chữ khung dựng tự sinh (edl, overlays, captions, srt); **không đọc chữ trong ảnh/clip gốc** |
| Bản xem trước nhẹ (`adapters/preview_b.py`) | **Có** test với ffmpeg thật + `npx` giả. **Chưa** chạy với HyperFrames thật; giá trị `--quality` chưa kiểm chứng |
| Lệnh chế độ B trong `tools.json` | Chép từ SKILL.md của skill dựng video, **chưa chạy** từ khung này |
| Chế độ A, C, S | **Chưa có lệnh** (chờ skill). Chạy tới bước dựng sẽ báo rõ, không giả vờ |
| Bộ video mẫu tự động (nguyên tắc 7), PR tự sửa SKILL.md | **Chưa làm** (xem `golden/README.md`, `docs/KIEN-TRUC.md`) |

## Cài (Windows, Python ≥ 3.10)
```
cd vidagents
py -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"
copy config.example.json config.json      # sửa đường dẫn, ID Telegram
set ANTHROPIC_API_KEY=...                  # key API riêng (tính phí theo token)
set TELEGRAM_BOT_TOKEN=...
pytest                                     # 98 test, không cần mạng (một số test dùng ffmpeg, không có thì tự bỏ qua)
vidagents demo                             # chạy cả luồng bằng AI giả + LangGraph thật
vidagents doctor --config config.json      # kiểm cấu hình, xem skill đã khai báo lệnh gì
vidagents bot --config config.json         # chạy bot (kèm dọn thư mục làm việc mỗi giờ)
vidagents cleanup --config config.json     # dọn tay thư mục làm việc quá hạn
vidagents cost --config config.json        # chi phí AI theo job (thêm --job <mã> cho một job)
vidagents watchdog --config config.json    # báo Telegram video đứng; đặt Task Scheduler chạy mỗi 15 phút (bắt được cả lúc bot chết)
vidagents dashboard --config config.json   # bảng theo dõi job trên trình duyệt: http://127.0.0.1:8765 (chỉ xem)
vidagents demo --workdir D:\demo-data      # tạo dữ liệu thử; xem bằng: vidagents dashboard --demo-dir D:\demo-data
```
`pytest` có 125 test; nếu thư mục dự án nằm ở đường dẫn rất dài thì tạo `.venv` ở đường dẫn ngắn (Windows báo "filename or extension is too long" khi nạp một số thư viện).

## Chạy thử với Claude thật
Tính tiền theo token qua Claude API (tách với gói Claude đang dùng). Có hai cách đăng nhập:
1. **Không cần key (khuyên dùng khi chạy trên máy của mình):** cài CLI `ant` (bản Windows: `ant_<phiên bản>_windows_amd64.zip` ở github.com/anthropics/anthropic-cli/releases), chạy `ant auth login`, chọn tổ chức và workspace trong trình duyệt. SDK `anthropic` tự dùng đăng nhập này khi không có biến key (thư mục lưu: `%APPDATA%\Anthropic`). Tài liệu nói đăng nhập kiểu này dành cho máy cá nhân; bot chạy lâu trên máy chung nên dùng service account key hoặc Workload Identity Federation.
2. **Key:** đặt trong chính cửa sổ dòng lệnh, không ghi vào file hay chat. Có biến key thì key thắng đăng nhập `ant`.
```
set ANTHROPIC_API_KEY=...                  # PowerShell: $env:ANTHROPIC_API_KEY = "..."
vidagents demo --live --max-usd 1 --config config.json
```
- Gọi Claude thật cho các agent (brief, footage, script, critic, bài học); công cụ dựng/soi khung vẫn là giả, nên chỉ kiểm chứng phần AI (ép khuôn, chi phí, trần) và luồng. Mọi cổng duyệt do demo tự bấm.
- Từ chối chạy (và chưa gọi gì) nếu thiếu key, hoặc model nào chưa có giá trong `pricing` (không biết giá thì trần không có tác dụng).
- Chạm trần `--max-usd` thì dừng ngay, in số tiền đã dùng. Trần kiểm TRƯỚC mỗi lượt gọi nên có thể vượt tối đa một lượt.
- Cuối cùng in chi phí theo agent. Xem tiếp bằng `vidagents dashboard --demo-dir <thư mục demo>` (in ra cuối lần chạy).
- Chưa được chạy thật lần nào: phần này mới có test với Claude giả.

## Dashboard (chỉ xem)
`vidagents dashboard` đọc sổ job, checkpoint LangGraph và bảng chi phí rồi hiện: từng job đang ở bước nào (11 mốc), agent nào đang làm, nhật ký bước, phát hiện của critic, số vòng sửa so với giới hạn, chi phí AI so với trần, và job nào đang chờ người (kèm lệnh Telegram cần gõ). Không có nút duyệt/hủy: việc có hậu quả vẫn chỉ qua Telegram (nguyên tắc 6), nên dashboard không cần đăng nhập và mặc định chỉ nghe `127.0.0.1`. Đặt `--host 0.0.0.0` thì ai cùng mạng cũng xem được tên job, thương hiệu, chi phí. Agent "đang làm" được suy từ bước vừa xong, nên trong vòng sửa theo góp ý của người duyệt có thể chậm một nhịp; số "lượt AI" chỉ có khi chạy với Claude thật (AI giả của `demo` không ghi).
Khóa (key, token) chỉ đặt qua biến môi trường, không ghi vào file cấu hình hay chat.

## Footage trên NAS, mỗi job một thư mục riêng
- `footage_root` trỏ vào thư mục gốc trên NAS (nên dùng đường dẫn UNC `//TÊN-NAS/...`, không dùng ổ ánh xạ vì bot chạy nền thường không thấy). Tài khoản chạy bot cần quyền đọc, và quyền ghi vào `<footage>\edit\` để nhận kết quả.
- Khi nhận job, khung **chép các clip cấp trên cùng** (mp4/mov/mkv/m4v, ảnh, âm thanh; chỉnh ở `work_copy_exts`) về `work_root\<mã job>\` trên ổ nội bộ của máy chung. Skill chỉ làm việc trong bản chép này, nên hai job cùng footage không đè nhau và NAS không bị ghi gì trong lúc dựng. Thư mục con của footage bị bỏ qua (bot báo lại).
- Hết chỗ ổ đĩa (cần `dung lượng footage + work_reserve_gb`) hoặc NAS mất kết nối thì job bị hủy ngay lúc nhận, kèm lý do.
- Xong job, kết quả (bản gốc, bản nhẹ, phụ đề, script.md, edl.json, brief.md) được chép về `<footage gốc>\edit\<mã job>\`. Chỉ khi chép về thành công thì bản làm việc mới được phép dọn: giữ thêm `work_keep_hours` giờ (mặc định 24) rồi tự xóa. Job đang chạy/chờ duyệt, hoặc xong mà chưa về được NAS, không bao giờ bị dọn.

## Lệnh Telegram
`/video "<thư mục footage>" <loại> <thương hiệu>` + brief ở các dòng sau · `/duyet <job>` · `/sua <job> <góp ý> [chedo=A|B|C|S]` ·
`/bosung <job> <nội dung>` · `/chapnhan <job>` · `/baihoc <job> 1,3 | ghim 1 | bo` · `/huy <job>` · `/trangthai` (kèm chi phí AI) · `/nangtran <job> <USD>` · `/tieptuc <job>` · `/loai`.
Mỗi người nhắn riêng cho bot một lần (`/start`) để bot gửi được tin riêng.

## Thả skill vào
**Từ 19/11/2026:** `agents/<tên>/SKILL.md` được **sinh tự động** từ skill dựng video (repo `skill-edit-video-hoi-cho`, máy này ở `D:\skill-edit-video-hoi-cho`) bằng `py tools\sync_vidagents.py --agents <thư mục agents này>`: mỗi file = `ROLE.md` của agent (vai trò trong khung, sửa ở đây) + các mục trích từ SKILL.md của skill mà agent cần (4,5–12 nghìn ký tự thay vì ~55 nghìn). Đừng sửa tay `SKILL.md`; `--check` báo lệch. Cách cũ dưới đây vẫn đúng cho skill khác.
1. Chép nội dung skill vào `agents/<tên>/SKILL.md` (xóa dòng `<!-- PLACEHOLDER -->`).
2. Khai báo lệnh trong `agents/<tên>/tools.json` cho từng chế độ (xem mẫu chế độ B). Đặt `heavy: true` cho bước cắt/render để xếp hàng suất render; `requires` để chặn khi chưa duyệt; `outputs` để khung kiểm file có thật.
3. `vidagents doctor` → bảng ✔/✘ cho biết còn thiếu lệnh nào.
4. Nếu skill đổi khuôn EDL của chế độ nào, thêm bộ kiểm trong `schemas.validate_edl`.
Khung (code) không phải sửa khi chỉ đổi cách làm bên trong skill, miễn đầu ra đúng khuôn.

## Chi phí, trần và cảnh báo
- Mỗi lượt gọi AI được ghi token + tiền theo job. Đặt `max_cost_per_job_usd` để có trần: chạm trần thì job DỪNG (không mất tiến độ), người giao nhận tin và gõ `/nangtran <job> <USD>` để chạy tiếp. Model chưa có giá trong `pricing` thì chỉ đếm token, không đoán tiền.
- Video đứng quá `stall_minutes` phút (đang chạy/xếp hàng mà không nhúc nhích) thì báo người giao + admin, mỗi lần đứng báo một lần. Job chờ người duyệt không tính là đứng.
- `brand.json` có khóa riêng theo thương hiệu (`brand.py`: `brand_lock`, `update_brand`). Bước nào ghi vào brand.json thì đặt `"brand_lock": true` trong `tools.json` (mặc định chưa bước nào bật).
- Cờ `"soft": true` trong `tools.json`: bước hỏng thì job vẫn đi tiếp và người giao được báo (dùng cho bản xem trước). Thiếu `requires` (chưa duyệt) thì không bao giờ được coi là lỗi nhẹ.

Chi tiết kiến trúc: `docs/KIEN-TRUC.md`.
