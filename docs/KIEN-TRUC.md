# Kiến trúc

## 1. Gộp 8 agent trong sơ đồ thành 1 điều phối + 4 agent
| Sơ đồ gốc | Trong khung | Lý do gộp |
|---|---|---|
| Orchestrator | **Graph** (code) + skill `orchestrator` (trích brief, rút bài học) | Luồng, cổng duyệt, giới hạn vòng phải cố định bằng code |
| Script Agent | `script` | Giữ riêng: viết kịch bản/EDL, chỉ cần brief + báo cáo footage |
| Vision + Clip Selection + phần nghe của Audio | `footage` | Cùng đọc một nguồn (footage + transcript), chuyển giao giữa chúng chỉ tốn token |
| Editing + Style + Audio + Subtitle | `edit` | Dính chặt vào nhau; việc nặng do script chạy, AI chỉ sửa EDL |
| Quality | `critic` | Giữ riêng và độc lập với edit để không "tự chấm bài mình" |

## 2. Luồng
```
intake → extract_brief ⇄ ask_info → footage → script ⇄ ask_info → ⏸ approve_1
       → edit_build → critic(preview) ⇄ edit_fix → ⏸ approve_2 → render → critic(final) → deliver
       → lessons → ⏸ lesson_gate
(mọi chỗ vượt giới hạn → ⏸ escalate: hỏi người giao, không tự quyết)
```
⏸ = `interrupt`: job dừng, trạng thái lưu vào checkpointer, tiếp tục khi người giao trả lời (có thể sau nhiều giờ, kể cả khi máy khởi động lại).

## 3. 7 nguyên tắc nằm ở đâu
1. **Chỉ chia agent khi có lợi** → 4 agent như bảng trên.
2. **Giao thức chung** → `schemas.py` (BriefExtract, ScriptDoc, FixPlan, CriticReport, Payload…). AI bị ép trả đúng khuôn; EDL sai khuôn thì thử lại 1 lần rồi dừng. Agent chỉ trao đổi qua file trong `<footage>\edit\`.
3. **Critic 2 lớp** → `nodes.critic`: lớp 1 là script của skill (ưu tiên `report.json`), lớp 2 là AI xem khung hình. Kết luận "có lỗi nặng" do code tính, không do AI.
4. **Lưu trạng thái** → checkpointer SQLite của LangGraph, thread_id = mã job; `/tieptuc` chạy tiếp từ bước lỗi.
5. **Giới hạn vòng** → critic tối đa `max_critic_rounds` (2) vòng sửa; người duyệt tối đa `max_revisions` (5) lần sửa; hỏi thêm thông tin tối đa 3 lần. Vượt thì `escalate`.
6. **Việc có hậu quả cần người** → `edit_build` cần `duyet_1`, `render` cần `duyet_2` (kiểm ở node **và** ở `tools.json` qua `requires`); chi phí ngoài hiện ở duyệt 1; hệ thống không có đường nào đăng bài; sửa nội dung sau duyệt 2 thì xóa duyệt 2, phải duyệt lại; chỉ người giao được duyệt (admin chỉ được hủy).
7. **Bộ đánh giá** → mới có nền: mỗi bước ghi `skill_version`. Bộ chạy tự động chưa làm.

## 4. Học và sửa skill
- Cuối job, `lessons` đề xuất bài học cho từng agent; người giao chọn giữ bài nào (`/baihoc`). Chỉ bài lặp lại ≥ 2 lần hoặc được ghim mới thành quy tắc mặc định. Bài học có vẻ cố bỏ quy tắc cứng/bỏ bước duyệt bị loại.
- `critic` chỉ học từ lỗi mà **người duyệt bắt được còn critic bỏ sót** (`human_miss`).
- Bài học nằm ở `agents/<tên>/lessons.jsonl` (cùng repo với skill). Đề xuất chờ duyệt ở `lessons_pending.jsonl` (không commit).
- Sửa chính `SKILL.md` bằng pull request tự động: **chưa làm**. Hiện sửa tay qua PR, khi chạy ổn mới tự động hóa.

## 5. Khóa suất render
Cắt 4K/render là việc nặng nên xếp hàng theo suất: `render_slots` (mặc định 1; đặt theo cấu hình từng máy, máy render thật cấu hình cao nên cần đo rồi mới nâng số suất; máy thử 7,8 GB RAM chỉ để chạy thử). Bước nào `heavy: true` trong `tools.json` thì giữ một suất. Nhiều job vẫn cùng tiến ở các bước nhẹ (kịch bản, chờ duyệt, critic). Khóa là file trong `locks_dir`, hết hạn sau `lock_stale_s`; kẹt thì `vidagents unlock`.
**Windows (sửa 14/11/2026):** Windows không cho xóa file đang bị luồng/tiến trình khác mở, và báo `PermissionError` (không phải `FileExistsError`) khi tạo file khóa đang chờ xóa. Bản cũ nuốt lỗi khi trả khóa nên file khóa bị bỏ rơi, cả hàng chờ đứng tới khi quá hạn (test `test_concurrent_updates_...` treo vì vậy). Nay `release` thử xóa lại tối đa ~3 giây rồi ghi cảnh báo (không im lặng nữa), và `_try` coi `PermissionError` là "suất đang bận". Nguyên nhân cơ chế là suy luận từ dấu vết (file khóa của job đã thoát còn lại, các luồng khác chờ mãi) chứ chưa bắt tận tay cuộc đua; sau sửa, bộ test chạy hết không treo. Hạn chế: nếu thư mục khóa bị cấm ghi, `PermissionError` cũng bị coi là bận (chờ tới `lock_timeout_s` thay vì báo lỗi ngay).

Khóa riêng cho thương hiệu: `brand.py` (`brand_lock` theo tên thương hiệu, `update_brand` ghi nguyên khối + giữ 5 bản sao lưu); bước trong `tools.json` có `"brand_lock": true` chạy trong khóa này (thứ tự cố định: khóa thương hiệu rồi mới tới suất render, tránh khóa chéo). Mặc định CHƯA bật cho bước nào vì chưa biết chắc bước nào của skill ghi vào `brand.json`; bật khi xác định được. Chưa kiểm chứng: có tiến trình nào ngoài khung (người sửa tay bằng Notepad) ghi cùng lúc thì khóa không chặn được.

## 5c. Chi phí AI, trần chi phí, báo video đứng
`usage.py`: bảng `usage` trong sổ job ghi từng lượt gọi (agent, model, token vào/ra/bộ nhớ đệm, tiền). Tiền = token × `pricing` trong config; model thiếu giá thì `usd = null` và chỉ đếm token. Trần: `max_cost_per_job_usd` hoặc trần riêng từng job (`/nangtran`). Kiểm trần TRƯỚC mỗi lượt gọi, nên có thể vượt trần tối đa bằng một lượt. Chạm trần ném `CostCapExceeded`, `runner._blocked` đặt job `blocked` và báo người giao + admin; tiến độ nằm ở checkpointer nên `/nangtran` rồi chạy tiếp từ node đang dừng. `watchdog.py`: job `queued/running` không có cập nhật quá `stall_minutes` thì báo (người giao + admin) một lần cho mỗi mốc cập nhật (`alerted_at`). Luồng nền trong bot không bắt được lúc chính bot chết: dùng `vidagents watchdog` chạy từ Task Scheduler.

## 5b. Thư mục làm việc riêng từng job (`workspace.py`)
Lý do: `make_video.py` gắn cứng thư mục kết quả là `<footage>\edit\` và coi một thư mục footage là một video; hai job cùng footage sẽ đè file nhau (brief, edl, bản render). Cách xử lý không cần sửa skill: `intake` chép clip cấp trên cùng về `work_root\<mã job>\` (ổ nội bộ), skill chạy trên bản chép đó; `deliver` chép kết quả về `<footage gốc>\edit\<mã job>\` rồi đánh dấu `.delivered`; `cleanup_work` (mỗi giờ khi bot chạy, hoặc `vidagents cleanup`) xóa bản chép của job xong/hủy sau `work_keep_hours` giờ. Không dọn: job đang chạy/chờ, job xong chưa có `.delivered`, thư mục lạ không thuộc job nào. Chưa xử lý: thư mục con của footage không được chép (script B chỉ đọc cấp trên cùng; chế độ A/C/S có thể cần khác khi skill vào), chép file lớn qua NAS chưa có thanh tiến độ.

## 6. 10 dạng video
`video_types/*.json`, lấy từ sơ đồ. **Chế độ đề xuất là phỏng đoán của mình, cần Duylibee chốt** (dạng `auto` thì AI đề xuất, người giao xác nhận ở duyệt 1). Độ dài lấy từ ví dụ trong sơ đồ, chỉ là gợi ý. Dạng khách hàng (phỏng vấn, feedback) bắt buộc ghi rõ khách đã đồng ý. Dạng ưu đãi bắt buộc đủ ưu đãi, hạn, nơi mua. Chế độ S (từ slide) để dành chỗ cho skill dựng video đào tạo từ slide, chưa có lệnh.

## 7. Điểm cần biết khi tích hợp skill chế độ B hiện có
Skill B duyệt bản dựng bằng HyperFrames Studio và chỉ chạy critic **sau khi render**. Khung bổ sung bản xem trước không sửa skill: `adapters/preview_b.py` gọi `hyperframes render` của skill, kiểm bằng ffprobe (30 fps, độ dài khớp `timeline.json`), ép xuống 540 px (~nhẹ để gửi Telegram), rồi `critic_preview` chạy `critic.py` trên file đó. Cả hai bước có cờ `soft`: hỏng thì job vẫn đi tiếp, người giao được báo, và bản xem trước cũ không bao giờ bị báo nhầm là bản mới. **Giá phải trả:** bản xem trước là một lần render HyperFrames đầy đủ (suất render, `heavy`), nên mỗi video tốn thêm thời gian render một lần nữa trừ khi `preview_quality` giảm được (giá trị `--quality` của HyperFrames CHƯA kiểm chứng, để trống thì không gửi cờ). `rebuild` vẫn chưa khai báo.

**Đối chiếu brief (`factcheck.py`)**: critic tự so giá, %, ngày, "mua X tặng Y" trên phụ đề/đồ họa (edl, overlays, captions, srt) với brief + brand.json. Trên màn hình mà brief không có = lỗi NẶNG (chương trình quyết); chỉ có trong lời kịch bản = vừa; dạng video bắt buộc ưu đãi/hạn mà màn hình không thấy = vừa. Giới hạn: không đọc chữ trong ảnh/clip gốc (không OCR); "2/3 chai" có thể bị đọc nhầm thành ngày 02/03 (lỗi báo thừa, người duyệt quyết).

## 7b. Dashboard (`dashboard.py`, `web/index.html`)
Lớp xem, tách khỏi runner: `Reader` đọc `JobStore` (sổ job), checkpoint LangGraph (`SqliteSaver.get_tuple`, lấy `steps`, `approvals`, `critic_findings`, `escalation`...) và `UsageMeter` (chi phí), không ghi gì. Máy chủ là `http.server` có sẵn của Python (không thêm thư viện), chỉ trả lời GET, các phương thức khác trả 405. Tiến độ = số mốc (trong 11 mốc `MILESTONES`) đã có bước tương ứng trong `steps` hoặc đã có `approvals.duyet_1/duyet_2`. Không đọc được checkpoint (file hỏng, đang khóa lâu) thì vẫn hiện theo sổ job kèm cảnh báo, không làm sập. Chưa làm: nút duyệt trên web (cần đăng nhập và phân quyền, xem nguyên tắc 6), đẩy realtime (đang hỏi lại mỗi 2 giây), lịch sử nhiều ngày, lọc theo thương hiệu.

## 8. Việc chưa làm
Chạy kiểm chứng với LangGraph/Claude API/Telegram/HyperFrames thật (`vidagents demo` chưa chạy được ở nơi dựng khung) · đối chiếu bảng giá chính thức cho `pricing` · lệnh chế độ A, C, S · bộ video mẫu tự động · PR tự sửa skill · gửi brief dạng file đính kèm · OCR chữ trong ảnh/clip gốc · chọn bước nào của skill cần `brand_lock`.
