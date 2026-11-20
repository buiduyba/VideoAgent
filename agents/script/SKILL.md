<!-- SINH TỰ ĐỘNG bởi skill-edit-video-hoi-cho/tools/sync_vidagents.py từ commit 691c849. Sửa vai trò ở ROLE.md (cùng thư mục), sửa nội dung skill ở repo skill rồi chạy lại script; đừng sửa tay file này. -->

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



# Trích từ skill dựng video (chỉ các mục agent 'script' cần)

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

## Nền tảng
| Nền tảng | Khung | Âm lượng |
|---|---|---|
| Reels / TikTok | 1080×1920 @30 — vùng an toàn: chừa ~250 px dưới, ~120 px phải, ~200 px trên | -14 LUFS, TP ≤ -1 dBTP |
| Facebook bảng tin | 1080×1350 (mặt ở 1/3 trên) | -14 LUFS |
| YouTube / web | 1920×1080 | -14 LUFS |
Luôn xuất kèm `.srt` và bản nhẹ ~40 MB để đăng.

### B1. Brief (hỏi một lần, gợi ý sẵn mặc định)
Thương hiệu · tên video · độ dài · khung · mục tiêu (quảng cáo bán hàng / recap sự kiện) · giá còn áp dụng không · nơi mua · câu bắt buộc giữ/bỏ · nhạc A (tươi) / B (ấm) · phong cách dựng. Có brief thì EDL đầu tiên trúng ngay.

**Phiếu brief bằng file (khỏi hỏi đi hỏi lại):** đo thực tế ở bản hội chợ — thiếu brief tốn thêm 2–3 lượt chờ, ~10 phút/video.
1. Người dùng đã gửi brief (link Claude Doc, `brief.md/.txt/.docx` trong thư mục video hoặc `edit\`) → đọc, dùng luôn.
2. Chưa có → **việc đầu tiên khi gọi skill**: làm theo "Bước mở đầu chung" ở trên (hộp chọn thư mục + phiếu hiện trong chat, ghi vào `edit\brief.md`). Chỉ khi không có công cụ widget hoặc người dùng muốn điền file thì chép `{SKILL}\assets\brief-template.md` thành `<thư mục video>\edit\brief.md` (không ghi đè nếu đã có), **điền sẵn** những gì đã biết và ghi nguồn trong ngoặc (từ `_brands\<brand>\`, từ `edit\project.md` cũ, thư mục footage người dùng vừa đưa). **Không tự điền giá, ưu đãi, lợi ích, CTA.**
3. Mở file cho người dùng (`Start-Process notepad.exe "<đường dẫn>"` nếu app không có khung mở file) và nhắn 1–2 câu: "Điền các mục có \* rồi lưu, nhắn **xong**." Không dán lại nội dung phiếu vào chat. Người dùng thích trả lời ngay trong chat → ghi vào `brief.md` giúp họ.
4. **Trong lúc chờ** chạy luôn `MV prep` (máy tự xử lý, không cần brief). Người dùng nhắn "xong" → đọc lại `brief.md`.
5. Mục bắt buộc (\*) còn thiếu → hỏi **một lần, gom tất cả** trong một tin nhắn; không bịa. "Câu chữ không được dùng" và "Thương hiệu khác cần tránh lộ" là ràng buộc cứng: chép vào `brand.json` (`claims_avoid`), soát khi xem contact sheet và mọi chữ trên màn hình. Ghi tóm tắt brief vào đầu `edit\project.md`.

### B3. Chiến lược + `edl.json` (duyệt 1)
Cấu trúc booth/KOC đã chạy tốt: **HOOK (lợi ích chính, ≤ 5 s) → GIỚI THIỆU → thẻ CÔNG DỤNG → CÁCH DÙNG (cơ chế, liều lượng) → AN TOÀN → thẻ KẾT QUẢ (bằng chứng trực quan) → GIÁ → BẰNG CHỨNG XÃ HỘI (khách chốt đơn) → CTA nơi mua → end card**.
```json
{"sources": {"A": "W:/…/clip1.mp4", "F": "W:/…/canh-rong.mp4"},
 "ranges": [{"n": 1, "source": "C", "start": 107.15, "end": 110.06, "beat": "HOOK", "quote": "…", "reason": "…"},
            {"n": "1b", "source": "C", "start": 110.69, "end": 111.36, "beat": "HOOK", "quote": "chỉ ngâm thôi."}],
 "broll":  [{"after_range": "1b", "source": "C", "start": 0.0, "end": 1.2}],
 "covers": [{"range": 1, "source": "E", "start": 264.0, "dur": 1.5, "why": "1,5 s đầu thấy sản phẩm hoạt động"},
            {"range": 4, "source": "B", "start": 30.9, "why": "cận nồi khi nói về xoong nồi"}],
 "gaps":   [{"before_range": 3, "seconds": 1.0, "card": "CÔNG DỤNG", "source": "F", "start": 4.0},
            {"after_range": 15, "seconds": 3.0, "card": "END", "source": "F", "start": 22.0}],
 "graphics": {"hook": ["KHÔNG CẦN CHÀ VÒ", "CHỈ NGÂM LÀ SẠCH"], "hook_top": 640,
              "info": [{"range": 7, "title": "QUẦN ÁO", "rows": ["2–3 nắp bột", "2 lít nước", "Nước nóng 60°C"], "from_off": 0.3, "to_off": 0.6},
                       {"range": 13, "title": "GIÁ HỘI CHỢ", "rows": ["110K / chai"], "from_off": 1.6, "note": "*Ưu đãi tại hội chợ"}],
              "punch": [{"range": "1b", "scale": 1.1}, {"range": 11, "scale": 1.25, "origin": "45% 65%"}]}}
```
- `n` dạng `"1b"` = phần nối của cùng câu (dùng để **bỏ khoảng lặng/lời đệm giữa câu**); phụ đề coi là cùng câu. Chỗ nối cùng góc máy → thêm `punch` khác tỉ lệ để che nhảy hình.
- `covers`: phủ hình khác lên lời (đang nói nhưng hình xấu/rộng); `dur` = chỉ phủ N giây đầu. `skip: true` = dự phòng.
- `gaps`: thẻ chuyển phần 1 s (`card` ≠ "END") hoặc end card (`"END"`, 3 s); `source/start` = hình nền mờ dưới thẻ.
- `graphics.info`: thẻ trắng bên trái, `from_off/to_off` giây lệch so với đầu/cuối đoạn, `to_range` để kéo qua đoạn sau.
- **Chọn take**: trọn câu, không vấp, âm rõ; mỗi ý một lần. Nhiều take → có thể đề xuất tác tử con "editor" (hỏi trước).
- Trình bày bảng (# · ý · clip+giây · lời · lý do) + đoạn bỏ + độ dài ước tính → chờ duyệt.

### B4b. Phong cách dựng (`--style`)
Ba phong cách đi kèm trong `{SKILL}\assets\styles\`; mỗi phong cách là một file JSON. **Không chọn `--style` = hành vi cũ của skill** (không căn beat). Chọn theo sản phẩm + khán giả, nêu lý do và hỏi xác nhận ở duyệt 1 (có thể gợi ý theo `style_goi_y` của dạng video).
| Phong cách | Hợp với | Khác biệt khi dựng |
|---|---|---|
| `ban-nhanh` | hàng tiêu dùng, giá/ưu đãi là chính | diện mạo mặc định + **SFX và thẻ vào đúng phách**; **nháy sáng** khi sang phần mới; vào cảnh phủ/b-roll cắt thẳng; nhạc A (tươi) |
| `muot` | quần áo, mỹ phẩm, đồ gia dụng đẹp; KOC kể trải nghiệm | hiệu ứng chữ chậm êm, thẻ bo tròn, **mờ dần / blur** khi vào cảnh phủ, **vệt sáng** vào thẻ chuyển phần, chỉ giữ chime ở end card, nhạc B (ấm), màu `tu_nhien` |
| `trend` | demo nhanh (xoong nồi, dụng cụ), khán giả trẻ | hiệu ứng chữ gấp, **lia / zoom / đẩy** khi vào cảnh phủ (kèm whoosh nhỏ), **nháy sáng** ở thẻ, SFX + căn beat, phụ đề và tiêu đề thẻ chuyển phần lớn hơn |
- File style có: `music` (`default`/`alt`), `anim` (nhịp/easing hiệu ứng chữ), `sfx` (`false` = chỉ chime), `beat_snap`, `transitions` (chuyển cảnh, xem dưới), `grade_preset` (kiểu màu, thắng `brand.json`, thua `--grade` và `edl.json`), `grade` (bộ lọc ffmpeg thêm vào cuối), `css` (chỉnh giao diện), `pacing` (**gợi ý** độ dài cảnh phủ/thẻ khi viết `edl.json`, không tự áp). Muốn phong cách riêng: chép một file vào `{PROJECTS}\_styles\<tên>.json` (ưu tiên hơn bản đi kèm) hoặc đặt `"style": "<tên>"` trong `edl.json`.
- **Căn beat** (`beat_snap`): cần `brand.json` → `music.bpm` (`{"default":112,"alt":90}`, đã có trong mẫu cho 2 bản nhạc tổng hợp; nhạc riêng của thương hiệu thì tự khai bpm, không khai = bỏ qua). SFX và thẻ thông tin bị kéo về phách gần nhất nếu lệch ≤ 0,2 s và thẻ vẫn nằm trong đoạn; thẻ chuyển phần đứng theo điểm cắt nên không dời; chuyển cảnh cũng đứng theo điểm nối nên không dời.
- **Lưu ý màu da:** các phong cách cố ý **không** tăng bão hòa/ấm vùng sáng thêm trên `tuoi_min` (bài học OxiClean: tăng mạnh đẩy da sang vàng cam). Muốn màu khác → thử trên 3–4 khung có mặt người như mục B6.
- **Nhiều video từ một lần prep:** cùng footage + cùng `edl.json`, chạy `MV build` nhiều lần với `--style` và `--name` khác nhau (mỗi lần ra một project riêng); khác nội dung thì thêm `--edl "<file>.json"`. Máy yếu: chạy lần lượt, không chạy song song (cắt 4K + render ngốn RAM).
- **Chuyển cảnh ở ba chỗ nối** (`transitions` trong style; chạy khi có `--style`): `phu` = nối vào/ra **cảnh phủ và b-roll** (cả lúc hết cảnh phủ, trở lại người nói), `the` = nối từ lời vào **thẻ chuyển phần / end card**, `phan` = nối từ thẻ sang **phần mới**. Mỗi chỗ đặt một kiểu hoặc một mảng xoay vòng, thêm `dur_phu` / `dur_the` / `dur_phan` (giây; mặc định 0,3). Kiểu có: `cut` (không hiệu ứng) · `fade` · `blur` · `zoom` · `whipL` · `whipR` · `pushU` · `flash` · `leak`.
  - **Không bao giờ chạm điểm cắt trong lời:** nối lời → lời (kể cả đổi phần mà không có thẻ) không có chuyển cảnh. Tiếng giọng không bị đụng; chỉ hình bị che.
  - Đây là hiệu ứng **một lớp che điểm cắt** (nháy, mờ, phóng, lia, vệt sáng) chạy trên lớp video nền, chữ/thẻ/phụ đề nằm trên nên đứng yên. Không phải hòa chồng hai cảnh thật (không có đệm vì đoạn lời cắt đúng ranh giới từ, chồng lên sẽ thấy miệng nói tiếp mà không có tiếng). Muốn hòa chồng thật: Chế độ C.
  - Hai hiệu ứng cách nhau < 0,6 s thì bỏ cái sau (in log); đoạn quá ngắn (< ~0,5 s hai bên) tự rút ngắn hoặc bỏ. Danh sách chuyển cảnh ghi trong `assets\overlays.json` (`transitions`). Project dựng bằng `--skip-base` từ bản cũ không có thông tin cảnh phủ → chỉ còn chuyển cảnh ở b-roll/thẻ.
- Chạy nhiều video song song (hàng loạt) và chuyển cảnh chồng hai cảnh thật chỉ có ở **Chế độ C** (`references\che-do-c.md`, Bước 8b–8c).
