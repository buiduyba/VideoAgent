# Bộ video mẫu (golden) — nguyên tắc 7

Chưa có bộ chạy tự động. Hiện chỉ có phần nền: mỗi bước trong nhật ký job (`state.steps`) ghi `skill_version`
(dấu vân tay SKILL.md + tools.json của agent). Khi sửa skill, so sánh kết quả cùng một brief + footage giữa hai phiên bản.

Dự kiến mỗi video mẫu là một thư mục: `brief.md`, `expected.json` (giá/ưu đãi phải xuất hiện, số lỗi nặng tối đa,
độ dài tối đa) và link tới footage. Quy tắc gộp PR sửa skill: chạy lại toàn bộ bộ mẫu, chất lượng không tụt mới gộp.
