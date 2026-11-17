#!/usr/bin/env python3
"""Bản xem trước nhẹ cho chế độ B (chỉ dùng thư viện chuẩn của Python).

Làm gì: render project HyperFrames đã dựng -> kiểm tra bằng ffprobe -> ép xuống 540 px chiều ngang, nhẹ để gửi Telegram
và để critic soi trước "duyệt 2". File của skill không bị sửa; adapter chỉ GỌI lệnh `hyperframes render` của skill.

Dùng:  py preview_b.py --project <thư mục project> --name <tên video> --edit-dir <thư mục edit>
                        [--npx npx.cmd] [--ffmpeg ffmpeg] [--ffprobe ffprobe] [--quality <giá trị>] [--timeout 3600]
Mã thoát: 0 xong · 2 render lỗi · 3 chưa dựng / video không đạt kiểm tra · 4 ép nhẹ lỗi.
CHƯA KIỂM CHỨNG: giá trị của --quality (tên và các mức) phụ thuộc bản HyperFrames đang cài. Không truyền --quality thì adapter
KHÔNG gửi cờ đó (render đủ chất lượng, an toàn nhưng không nhanh hơn). Xem `npx.cmd hyperframes render --help` rồi đặt "preview_quality".
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FPS = 30.0  # skill dựng 1080x1920 @30
FPS_TOL = 0.1
DUR_TOL = 0.5  # giây


def run(cmd, cwd=None, timeout=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "hết thời gian chờ"
    except FileNotFoundError as e:
        return 127, f"không tìm thấy chương trình: {e}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def fraction(s: str) -> float:
    a, _, b = (s or "0/1").partition("/")
    try:
        return float(a) / float(b or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(ffprobe: str, path: Path) -> dict:
    code, out = run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,avg_frame_rate:format=duration", "-of", "json", str(path)], timeout=120)
    if code != 0:
        raise ValueError(f"ffprobe lỗi: {out[-300:]}")
    return json.loads(out or "{}")


def expected_seconds(proj: Path) -> float | None:
    try:
        tl = json.loads((proj / "assets" / "timeline.json").read_text(encoding="utf-8"))
        return float(tl[-1]["out_start"]) + float(tl[-1]["dur"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def check(info: dict, expected: float | None) -> list[str]:
    errs = []
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        return ["file không có luồng hình"]
    fps = fraction(v.get("avg_frame_rate", ""))
    if abs(fps - FPS) > FPS_TOL:
        errs.append(f"tốc độ khung {fps:.2f} fps, cần {FPS:g} (critic đếm khung theo {FPS:g} fps)")
    try:
        dur = float(info.get("format", {}).get("duration", 0))
    except ValueError:
        dur = 0.0
    if expected is not None and abs(dur - expected) > DUR_TOL:
        errs.append(f"độ dài {dur:.2f}s lệch quá {DUR_TOL}s so với bản dựng ({expected:.2f}s)")
    if dur <= 0:
        errs.append("độ dài bằng 0")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--edit-dir", required=True)
    ap.add_argument("--npx", default="npx.cmd" if os.name == "nt" else "npx")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--ffprobe", default="ffprobe")
    ap.add_argument("--quality", default="")
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args(argv)
    proj, edit = Path(a.project), Path(a.edit_dir)
    final = edit / f"{a.name}-xemtruoc.mp4"
    raw = proj / f"{a.name}-xemtruoc-goc.mp4"
    for p in (final, raw):  # bản cũ không bao giờ được lẫn vào bản mới
        p.unlink(missing_ok=True)
    if not (proj / "assets" / "timeline.json").exists():
        print(f"LỖI: {proj} chưa được dựng (thiếu assets/timeline.json)", file=sys.stderr)
        return 3
    cmd = [a.npx, "hyperframes", "render", "--output", str(raw)] + (["--quality", a.quality] if a.quality else [])
    code, out = run(cmd, cwd=str(proj), timeout=a.timeout)
    if code != 0 or not raw.exists():
        print(f"LỖI render xem trước (mã {code}): {out[-600:]}", file=sys.stderr)
        raw.unlink(missing_ok=True)
        return 2
    try:
        errs = check(probe(a.ffprobe, raw), expected_seconds(proj))
    except ValueError as e:
        errs = [str(e)]
    if errs:
        print("LỖI bản xem trước không đạt: " + "; ".join(errs), file=sys.stderr)
        raw.unlink(missing_ok=True)
        return 3
    tmp = proj / f"{a.name}-xemtruoc-nhe.mp4"
    code, out = run([a.ffmpeg, "-y", "-v", "error", "-i", str(raw), "-vf", "scale=540:-2", "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(tmp)], timeout=a.timeout)
    raw.unlink(missing_ok=True)
    if code != 0 or not tmp.exists():
        print(f"LỖI ép nhẹ bản xem trước (mã {code}): {out[-400:]}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return 4
    edit.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp), str(final))
    print(f"PREVIEW {final} ({final.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
