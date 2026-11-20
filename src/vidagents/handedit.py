"""Nhánh chỉnh tay bằng VideoFac (ở cổng Duyệt 2): đóng gói dự án lên NAS, kiểm bản xuất khi người chỉnh nhắn /xong.

Vì sao có nhánh này: `/sua` bắt AI làm lại và tốn tiền mỗi vòng; lỗi nhỏ về chữ/thời gian chỉnh tay trong VideoFac thì không tốn AI.

Luồng: /chinhtay -> `import.js --skill <dự án> --pack <NAS>\\edit\\<mã job>\\videofac` (gói dùng đường dẫn tương đối, mở được
trên máy khác) -> người chỉnh bấm Mo-trong-VideoFac.cmd, chỉnh, Xuất (bản xuất tự về `export\\` của gói) -> /xong -> bot kiểm bản xuất
-> giao. Bản chỉnh tay KHÔNG qua render HyperFrames và KHÔNG qua critic bản cuối: bot chỉ kiểm khung hình, tiếng, độ dài và đối
chiếu giá/ưu đãi trong phụ đề.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config


class HandEditError(Exception):
    pass


def pack_dir(source_dir: str | Path, job_id: str) -> Path:
    """Nơi đặt gói chỉnh tay: trong thư mục footage gốc, cạnh kết quả của job."""
    return Path(source_dir) / "edit" / job_id / "videofac"


def available(cfg: Config) -> str:
    """'' nếu dùng được, ngược lại là lý do (để báo người giao)."""
    if not cfg.videofac_import:
        return "Máy dựng chưa cấu hình VideoFac (khóa videofac_import trong config.json)."
    if not Path(cfg.videofac_import).is_file():
        return f"Không thấy import.js của VideoFac tại {cfg.videofac_import}."
    return ""


def pack(cfg: Config, *, project_dir: str | Path, edl: Path | None, name: str, dest: Path, timeout: int = 900) -> dict:
    """Đóng gói dự án HyperFrames thành gói chỉnh tay trong `dest`. Trả {dir, files, bytes, cmd, handover, exportDir}."""
    why = available(cfg)
    if why:
        raise HandEditError(why)
    cmd = [cfg.node_exe, str(cfg.videofac_import), "--skill", str(project_dir), "--pack", str(dest), "--name", name]
    if edl and Path(edl).is_file():
        cmd += ["--edl", str(edl)]
    try:
        # stdin=DEVNULL: không bao giờ chờ người gõ phím
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=timeout)
    except FileNotFoundError as e:
        raise HandEditError(f"Không chạy được Node ({cfg.node_exe}): {e}") from e
    except subprocess.TimeoutExpired as e:
        raise HandEditError(f"Đóng gói quá {timeout}s (NAS chậm?)") from e
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        raise HandEditError("Đóng gói lỗi: " + out.strip()[-400:])
    line = next((ln for ln in out.splitlines() if ln.startswith("RESULT ")), None)
    if not line:
        raise HandEditError("Đóng gói xong nhưng không đọc được kết quả: " + out.strip()[-300:])
    return json.loads(line[len("RESULT "):])


@dataclass
class Export:
    mp4: Path | None = None
    srt: Path | None = None
    info: str = ""
    problems: list[str] = field(default_factory=list)


def _probe(cfg: Config, path: Path) -> dict:
    r = subprocess.run([cfg.ffprobe, "-v", "error", "-show_entries", "format=duration:stream=width,height,codec_type",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    return json.loads(r.stdout or "{}")


def find_export(cfg: Config, pdir: str | Path, want: tuple[int, int] = (1080, 1920)) -> Export:
    """Bản xuất mới nhất trong `<gói>\\export` + các điều kiện tối thiểu. Không khẳng định "xem ổn": chỉ báo số đo được."""
    pdir = Path(pdir)
    ex = Export()
    lock = pdir / ".dang-mo-boi"
    if lock.exists():
        try:
            who = lock.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            who = "?"
        ex.problems.append(f"VideoFac vẫn đang giữ gói này ({who}). Chỉnh xong phải bấm 'Xuất video' (khóa tự mất khi xuất xong).")
    edir = pdir / "export"
    cands = sorted((p for p in edir.glob("*.mp4")) if edir.is_dir() else [], key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        ex.problems.append(f"Chưa có bản xuất .mp4 trong {edir}. Người chỉnh cần bấm 'Xuất video' trong VideoFac.")
        return ex
    mp4 = cands[0]
    handover = pdir / "handover.json"
    if handover.exists() and mp4.stat().st_mtime < handover.stat().st_mtime:
        ex.problems.append(f"Bản xuất mới nhất ({mp4.name}) cũ hơn gói chỉnh tay: chưa xuất lại sau khi mở.")
    try:
        info = _probe(cfg, mp4)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        ex.problems.append(f"Không đọc được {mp4.name} bằng ffprobe ({e.__class__.__name__}).")
        return ex
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    dur = float((info.get("format") or {}).get("duration") or 0)
    if not v:
        ex.problems.append(f"{mp4.name} không có hình.")
    elif (v.get("width"), v.get("height")) != want:
        ex.problems.append(f"Khung hình {v.get('width')}x{v.get('height')}, cần {want[0]}x{want[1]} (Reels/TikTok).")
    if not has_audio:
        ex.problems.append(f"{mp4.name} không có tiếng.")
    if dur < 1:
        ex.problems.append(f"{mp4.name} dài {dur:.1f}s, quá ngắn.")
    srt = mp4.with_suffix(".srt")
    ex.mp4, ex.srt = mp4, (srt if srt.exists() else next(iter(sorted(edir.glob("*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)), None))
    ex.info = f"{mp4.name} · {v.get('width') if v else '?'}x{v.get('height') if v else '?'} · {dur:.1f}s · {'có' if has_audio else 'KHÔNG có'} tiếng · {mp4.stat().st_size / 1048576:.1f} MB"
    return ex


def make_light(cfg: Config, src: Path, dst: Path) -> None:
    """Bản nhẹ để đăng/gửi (720 px, CRF 24)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([cfg.ffmpeg, "-y", "-v", "error", "-i", str(src), "-c:v", "libx264", "-preset", "medium", "-crf", "24",
                        "-vf", "scale=720:-2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=1800)
    if p.returncode != 0:
        raise HandEditError("Không tạo được bản nhẹ: " + (p.stderr or "")[-300:])
