<!-- PLACEHOLDER: bản tạm. Thả skill viết kịch bản/chiến lược thật vào đây (hook, chọn take, cấu trúc theo dạng video). -->
# Kịch bản & chiến lược (script)

Đầu vào: brief, brief đã trích, báo cáo footage, thông tin thương hiệu, góp ý của người duyệt (nếu có).
Đầu ra (ép theo khuôn ScriptDoc): `strategy_vi` (4-8 câu, lời thường), `rows` (bảng kịch bản), `estimated_seconds`, `style` (nếu có),
`external_costs`, `ad_risks`, `need_info`, `edl` (danh sách cắt theo khuôn của chế độ).

## Nguyên tắc
- Cấu trúc gợi ý: HOOK (lợi ích chính, ≤ 5 s) → giới thiệu → công dụng → cách dùng → bằng chứng → giá/ưu đãi (nếu brief có) → CTA → thẻ cuối.
- Mỗi ý một lần. Chọn câu trọn vẹn, không vấp.
- Lợi ích chỉ lấy từ brief hoặc lời người nói/chữ trên bao bì. Câu hứa mạnh, so sánh, "số 1": đưa vào `ad_risks`.
- Thiếu giá/ưu đãi/hạn/nơi mua mà video cần: ghi vào `need_info`, KHÔNG tự điền.
- Có chi phí ngoài (giọng AI...): ghi vào `external_costs` với số ký tự/ước tính.
- `edl` chế độ B: `sources`, `ranges` (n, source, start, end, beat, quote), tùy chọn `broll`, `covers`, `gaps`, `graphics`.
  Chế độ A/C/S: khuôn riêng sẽ khai báo khi thả skill thật vào (khung hiện chỉ kiểm tối thiểu).
- Có góp ý của người duyệt: sửa đúng chỗ họ nêu, giữ phần còn lại.
