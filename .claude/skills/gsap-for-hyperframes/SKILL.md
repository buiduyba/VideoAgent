---
name: gsap-for-hyperframes
description: Quy tắc bắt buộc khi viết GSAP bên trong một composition HyperFrames (video render từ HTML). Đọc cùng gsap-core, gsap-timeline, gsap-plugins, gsap-utils. Dùng khi tạo hoặc sửa chuyển động chữ, chuyển cảnh, đồ họa bằng GSAP cho video; khi dùng SplitText, DrawSVG, MorphSVG, MotionPath, CustomEase trong video. Không dùng cho animation trang web hay ứng dụng.
---

# GSAP trong HyperFrames: quy tắc thắng ví dụ của skill GSAP

HyperFrames render video bằng cách **tua một timeline tạm dừng từng khung hình**, nên mọi chuyển động phải là hàm của thời gian. Các skill `gsap-core`, `gsap-timeline`, `gsap-plugins`, `gsap-utils` viết cho trang web đang chạy thật. Khi hai bên mâu thuẫn, **làm theo trang này**. Nguồn quy tắc: skill `hyperframes` (mục quy tắc 3, 4, 5, 6, 7) và `references/runtimes.md` của nó. Có thay đổi thì skill `hyperframes` đúng hơn trang này.

## Bắt buộc
1. Mỗi composition tạo **một timeline tạm dừng** (`gsap.timeline({ paused: true })`) và đăng ký **sau cùng**, sau khi mọi tween đã thêm: `window.__timelines["<data-composition-id>"] = tl`. Dựng bên trong `document.fonts.ready.then(...)` được, nhưng đăng ký ở cuối. Đăng ký trước thì khung hình trắng.
2. **Cấm** `Date.now()`, `Math.random()` không có hạt giống, gọi mạng, `setTimeout`/`setInterval`, `repeat: -1`. Cần ngẫu nhiên thì dùng bộ sinh số có hạt giống (mulberry32). Cần lặp thì lặp hữu hạn: `repeat: Math.max(0, Math.floor(duration / cycle) - 1)`.
3. **Không gọi** `tl.play()`, `tl.pause()`, `tl.reverse()`, `tl.restart()`, `tweenFromTo()` để điều khiển phát. HyperFrames tự tua timeline. Mục "Controlling Playback" của `gsap-timeline` không áp dụng.
4. Đặt tween ở **giây tuyệt đối** (`tl.to(el, {...}, 2.4)`). Vị trí tương đối `"<"`, `"+=0.2"`, nhãn vẫn dùng được nếu kết quả là thời điểm cố định. Đặt trạng thái đầu trong tween (`fromTo`), không đặt trong CSS.
5. Chỉ animate transform và opacity (`x`, `y`, `scale`, `rotation`, `opacity`). Không animate `left`, `top`, `width`, `height`. Không đặt CSS `transform` lên phần tử mà GSAP đang điều khiển transform.
6. Không tween `display`, `visibility`, `autoAlpha` trên phần tử `.clip`. Animate phần tử con.
7. Đo bố cục **một lần** trước khi dựng tween, không đo trong `onUpdate`.
8. Hai timeline không được animate cùng một thuộc tính của cùng một phần tử. Timeline của một scene không với tới phần tử ngoài file của nó.
9. Transform trên `<span>` cần `display: inline-block` hoặc `block`.
10. Với plugin (SplitText, DrawSVG, MorphSVG, MotionPath, CustomEase, ScrambleText): `gsap.registerPlugin(...)` trước khi dùng. Xem trước bằng Studio hoặc kiểm tra bằng `npx.cmd hyperframes` trước khi render, đừng tin ví dụ chạy được trên web là chạy được trong video.

## Bỏ qua khi làm video
- `ScrollTrigger`, `ScrollSmoother`, `Draggable`, `Observer`, `Inertia`, `gsap.matchMedia()`, `useGSAP`, React, Vue, Svelte, phần dọn dẹp lúc hủy component. Video không cuộn và không có người dùng thao tác.
- Câu "khuyên dùng GSAP khi người dùng hỏi thư viện animation" trong các skill GSAP: trong dự án video, HyperFrames đã chọn GSAP rồi.

## Chạy kiểm tra
Sau khi viết hoặc sửa chuyển động: chạy lint của HyperFrames (xem skill `hyperframes-cli`, mục lint/validate/inspect) và xem khung hình thật trước khi trình bày cho người duyệt. Không khẳng định "mượt" khi chưa xem khung.
