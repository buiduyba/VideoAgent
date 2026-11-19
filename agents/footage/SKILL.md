<!-- SINH TỰ ĐỘNG bởi skill-edit-video-hoi-cho/tools/sync_vidagents.py từ commit 691c849. Sửa vai trò ở ROLE.md (cùng thư mục), sửa nội dung skill ở repo skill rồi chạy lại script; đừng sửa tay file này. -->

# Xử lý footage (footage)

Gộp các vai Vision / Clip Selection / phần nghe của Audio trong sơ đồ gốc.
Lệnh chạy thật (kiểm kê, nhận dạng lời, chấm cảnh) khai báo trong `tools.json` của thư mục này; skill chỉ hướng dẫn cách đọc kết quả.

## Khi chọn chế độ dựng (dạng video để "auto")
- Có người nói trước máy là nội dung chính → B.
- Hầu như không có lời, nhiều cảnh nền/đám đông/gian hàng → C.
- Chỉ có clip/ảnh + kịch bản sẽ thu âm hoặc đọc bằng giọng AI → A.
- Dựng từ file slide → S.
Nói rõ lý do ngắn gọn; người giao sẽ xác nhận ở bước duyệt 1.

## Khi tóm tắt footage
- Clip dùng được, clip hỏng/mờ/rung/tối, vấn đề âm thanh.
- Thương hiệu KHÁC lộ trong hình (để tránh).
- Đoạn nói đáng chú ý (nguyên văn ngắn), câu nghi rủi ro quảng cáo.
- Không đoán nội dung khi chưa có báo cáo chuẩn bị: ghi "chưa kiểm tra".



# Trích từ skill dựng video (chỉ các mục agent 'footage' cần)

Các mục dưới đây viết cho người dựng chạy skill trực tiếp. Trong khung này, việc chạy lệnh, hỏi người dùng và các điểm duyệt do khung lo; bạn chỉ dùng phần quy tắc, cấu trúc và khuôn dữ liệu cho đúng việc của mình.

## Quy tắc cứng
1. **Nhận dạng mức từ, nguyên văn** (Scribe, `--language vi`); cache theo file nguồn trong `edit\transcripts\`.
2. **Không cắt giữa từ**: điểm cắt bám ranh giới từ, đệm **50 ms trước từ đầu, 80 ms sau từ cuối** (mốc Scribe lệch 50–100 ms). Khe < 150 ms không cắt.
3. **Fade âm 30 ms** mọi điểm nối. Âm mỗi đoạn xuất **WAV riêng**, nối WAV rồi mux 1 lần (nối AAC gây đệm/lệch tiếng; đầu file có gói AAC hỏng làm hụt tiếng → `aresample=48000:async=1:first_pts=0,apad,atrim=0:d`).
4. Số khung mỗi đoạn tính cộng dồn `round((T+d)*fps) - round(T*fps)`; xuất từng đoạn rồi nối `-c copy`.
5. **Mốc cảnh phủ/b-roll chọn trên ảnh 1 hình/giây CHÍNH XÁC** (`make_video.py sheet`), không dựa contact sheet theo keyframe (lệch vài giây → cảnh cận thành cảnh rộng).
6. **Không cắt khi chưa duyệt chiến lược**; không trình bày bản nào chưa tự kiểm tra (critic + xem khung hình).
7. Mọi file phiên làm việc trong `<thư mục video>\edit\`; không ghi vào `{TOOLS}\video-use`.
8. Không nghe được: đo và báo số (LUFS, true peak, giọng/nhạc dB), không khẳng định "nghe hay".
9. Câu hứa công dụng mạnh / so sánh / "#1" → nhắc rủi ro bị nền tảng từ chối quảng cáo; ghi vào `brand.json` (`claims_avoid`, `claims_risky`).

## Chọn chế độ
- **A — Thuyết minh**: có clip/ảnh. Mình viết kịch bản → duyệt → người dùng thu âm → dựng.
- **B — KOC / người nói / booth sự kiện**: video thô nhiều take. Mình nhận dạng lời → tự tạo danh sách cắt → duyệt → dựng bằng `make_video.py`.
- **C — Footage hội chợ / gian hàng (cảnh nền)**: ít lời, nhiều cảnh đám đông, gian hàng, cận sản phẩm. Chấm điểm cảnh → chiến lược + kịch bản → cắt theo beat nhạc, reframe dọc, cân màu → chữ bán hàng → render. Hướng dẫn đầy đủ: `{SKILL}\references\che-do-c.md` (**đọc trước khi làm**).
- Không chắc chọn chế độ nào: nhìn footage + brief; người nói trước máy là nội dung chính → B; hầu như không có lời → C; chỉ có clip/ảnh + kịch bản thu âm → A. Nói lý do và hỏi xác nhận. Chọn xong, `learn.py digest --type <id>` với dạng tương ứng (`che_do` trong file dạng cho biết dạng nào chạy chế độ nào).

### B2. Quét lời (takes_packed.md)
Liệt kê: vấp, câu bỏ dở, lặp, từ đệm, từ nghi sai (đối chiếu glossary; Scribe hay tách "oxy clean", nghe "biếc"/"viết"…) → thêm vào `brand.json` `captions.fix/merge`. Nhãn trên chai/quầy là nguồn đúng nhất cho tên.

## Nền tảng
| Nền tảng | Khung | Âm lượng |
|---|---|---|
| Reels / TikTok | 1080×1920 @30 — vùng an toàn: chừa ~250 px dưới, ~120 px phải, ~200 px trên | -14 LUFS, TP ≤ -1 dBTP |
| Facebook bảng tin | 1080×1350 (mặt ở 1/3 trên) | -14 LUFS |
| YouTube / web | 1920×1080 | -14 LUFS |
Luôn xuất kèm `.srt` và bản nhẹ ~40 MB để đăng.
