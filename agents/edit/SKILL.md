<!-- PLACEHOLDER: bản tạm. Thả skill dựng video thật vào đây và khai báo lệnh build/render trong tools.json. -->
# Dựng (edit)

Gộp các vai Editing / Style / Audio / Subtitle trong sơ đồ gốc. Việc dựng, chỉnh màu, âm thanh, phụ đề, đồ họa, render do SCRIPT chạy (khai báo trong `tools.json`).
AI chỉ làm một việc: sửa `edl.json` khi critic hoặc người duyệt chỉ ra lỗi.

## Khi sửa EDL (khuôn FixPlan)
- Chỉ đổi đúng chỗ liên quan lỗi/góp ý; giữ nguyên phần còn lại và đúng khuôn EDL của chế độ.
- Liệt kê `changes` ngắn gọn. Việc không sửa được bằng EDL (đổi thông số thương hiệu, thiếu footage, cần quay lại) đưa vào `unfixable`; đừng giả vờ đã sửa.
- Không đổi giá, ưu đãi, hạn, CTA, lợi ích khác với brief. Muốn đổi thì đưa vào `unfixable` để người giao quyết định.
- Không chạm file gốc của người dùng.
