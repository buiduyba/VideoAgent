"""Thư mục làm việc riêng cho từng job.

Vì sao: script dựng video (make_video.py) gắn cứng thư mục kết quả là `<thư mục footage>\\edit\\` và coi một thư mục footage là
một video. Hai job cùng footage sẽ đè file của nhau. Nên mỗi job được chép các clip về `<work_root>\\<mã job>\\` (ổ nội bộ
của máy chung) và skill chỉ làm việc trong đó. Footage gốc trên NAS không bị ghi gì thêm cho tới lúc giao kết quả.

Vòng đời: chép (intake) -> dựng/render trong thư mục riêng -> chép kết quả về `<footage gốc>\\edit\\<mã job>\\` (deliver,
đánh dấu .delivered) -> giữ `work_keep_hours` giờ -> dọn (cleanup). Thư mục của job còn đang chạy/chờ duyệt không bao giờ bị dọn;
job xong mà chưa chép được kết quả về NAS cũng không bị dọn.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from .config import Config
from .locks import SlotLock

MARKER = ".delivered"
RESULT_DOCS = ("script.md", "edl.json", "brief.md")
TRANSCRIPTS = Path("edit") / "transcripts"   # nơi skill (video-use) để bản nhận dạng lời: <tên clip>.json
_MEDIA = {"video": (".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".m2ts", ".wmv", ".webm"),
          "ảnh": (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"),
          "âm thanh": (".mp3", ".wav", ".m4a", ".aac", ".flac")}


class StageError(Exception):
    """Lỗi chép footage (hết chỗ, không có clip, không đọc được NAS)."""


@dataclass
class StageResult:
    work_dir: Path
    copied: int = 0
    reused: int = 0
    total_bytes: int = 0
    skipped_dirs: list = field(default_factory=list)
    skipped_files: list = field(default_factory=list)
    transcripts_reused: int = 0   # bản nhận dạng lời lấy lại từ footage gốc (khỏi gọi Scribe, khỏi tốn tiền)


def media_summary(d: Path, depth: int = 3) -> str:
    """'3 video, 12 ảnh' trong một thư mục (đi xuống tối đa `depth` cấp, bỏ thư mục edit). Rỗng nếu không có gì."""
    count = {k: 0 for k in _MEDIA}

    def walk(p: Path, left: int) -> None:
        try:
            for x in p.iterdir():
                if x.is_dir():
                    if left > 1 and x.name.lower() != "edit":
                        walk(x, left - 1)
                    continue
                for k, exts in _MEDIA.items():
                    if x.suffix.lower() in exts:
                        count[k] += 1
                        break
        except OSError:
            pass

    walk(d, depth)
    return ", ".join(f"{n} {k}" for k, n in count.items() if n)


def _atomic_copy(src: Path, dst: Path) -> None:
    """Chép vào file tạm cùng thư mục rồi đổi tên một phát: người đọc (job khác đang lấy bản nhận dạng) chỉ thấy file đủ hoặc chưa có,
    không bao giờ thấy file đang chép dở. copy2 thẳng vào đích thì để lộ file dở; trên NAS chậm càng dễ trúng.
    Windows báo PermissionError khi hai luồng cùng đổi tên đè lên một file đích (hoặc có người đang mở nó): nếu đích đã có file
    thì job khác đã đưa lên đúng bản đó (cache theo tên clip), coi như xong; chưa có thì thử lại ít lần."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(f"{dst.name}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        shutil.copy2(src, part)
        for attempt in range(40):
            try:
                os.replace(part, dst)
                return
            except PermissionError:
                if dst.exists() or attempt == 39:
                    if dst.exists():
                        return
                    raise
                time.sleep(0.05)
    finally:
        part.unlink(missing_ok=True)


def seed_transcripts(src: Path, work: Path, files: list[Path]) -> int:
    """Chép bản nhận dạng lời đã có ở `<footage gốc>\\edit\\transcripts` sang bản làm việc, cho đúng các clip được chép.
    Skill thấy đã có thì không gọi Scribe nữa (cache theo tên clip). Không ghi đè bản đã có trong thư mục làm việc."""
    have, dst = Path(src) / TRANSCRIPTS, Path(work) / TRANSCRIPTS
    n = 0
    for f in files:
        t = have / f"{f.stem}.json"
        if t.is_file() and not (dst / t.name).exists():
            _atomic_copy(t, dst / t.name)
            n += 1
    return n


def return_transcripts(src: Path, work: Path) -> int:
    """Chép bản nhận dạng lời mới làm trong job về footage gốc để job sau dùng lại. Không đè file đã có ở NAS."""
    have, dst = Path(work) / TRANSCRIPTS, Path(src) / TRANSCRIPTS
    if not have.is_dir():
        return 0
    n = 0
    for t in have.glob("*.json"):
        if not (dst / t.name).exists():
            _atomic_copy(t, dst / t.name)
            n += 1
    return n


@contextlib.contextmanager
def footage_lock(cfg: Config, source: Path, owner: str, on_tick: Callable[[], None] | None = None) -> Iterator[int]:
    """Mỗi thư mục footage gốc chỉ MỘT job được nhận dạng lời (gọi Scribe, tính tiền) tại một thời điểm. Job đến sau chờ job trước làm xong,
    lấy bản nhận dạng của job trước rồi chạy tiếp (không gọi Scribe lần hai). Khóa theo đường dẫn đã chuẩn hóa nên viết hoa/thường hay dấu gạch
    ngược khác nhau vẫn là một thư mục. Khóa bằng file như khóa render nên bền qua tiến trình và tự hết hạn nếu tiến trình chết."""
    key = hashlib.sha1(os.path.normcase(os.path.normpath(str(Path(source).resolve()))).encode("utf-8")).hexdigest()[:12]
    lock = SlotLock(cfg.locks_dir, 1, cfg.lock_stale_s, poll_s=0.2, name=f"footage-{key}")
    with lock.hold(owner, timeout=cfg.lock_timeout_s, on_tick=on_tick) as slot:
        yield slot


def free_bytes(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _human(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def plan(cfg: Config, src: Path) -> tuple[list[Path], list[str], list[str]]:
    """Chỉ chép file cấp trên cùng có đuôi cho phép (script B cũng chỉ đọc cấp trên cùng). Thư mục con bị bỏ qua và báo lại."""
    files, skipped_dirs, skipped_files = [], [], []
    for p in sorted(src.iterdir()):
        if p.is_dir():
            if p.name.lower() != "edit":  # 'edit' là kết quả của lần dựng cũ, không phải footage
                skipped_dirs.append(p.name)
        elif p.is_file() and p.suffix.lower() in cfg.work_copy_exts:
            files.append(p)
        elif p.is_file():
            skipped_files.append(p.name)
    return files, skipped_dirs, skipped_files


def _same(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)


def stage_footage(cfg: Config, job_id: str, src: Path, on_start: Callable[[int, int], None] | None = None) -> StageResult:
    """Chép clip của job về thư mục riêng. Chạy lại an toàn: file đã chép đủ thì bỏ qua."""
    try:
        files, skipped_dirs, skipped_files = plan(cfg, src)
    except OSError as e:
        raise StageError(f"Không đọc được thư mục footage ({e.__class__.__name__}). NAS có đang kết nối không?") from e
    if not files:
        # hộp chọn thư mục của Windows không hiện file nên người giao dễ đưa nhầm thư mục cha: chỉ ra thư mục con nào có media
        subs = [(d, media_summary(src / d)) for d in skipped_dirs]
        subs = [f"{d} ({m})" for d, m in subs if m]
        hint = (" Media nằm trong thư mục con: " + "; ".join(subs[:6]) + ". Giao lại với đường dẫn tới thư mục con cần dựng."
                if subs else " Thư mục con không được chép.")
        raise StageError("Thư mục footage không có file clip/ảnh/âm thanh nào ở cấp trên cùng "
                         f"(đuôi nhận: {', '.join(cfg.work_copy_exts)})." + hint)
    work = cfg.work_path / job_id
    todo = [f for f in files if not _same(f, work / f.name)]
    need = sum(f.stat().st_size for f in todo)
    total = sum(f.stat().st_size for f in files)
    work.mkdir(parents=True, exist_ok=True)
    reserve = int(cfg.work_reserve_gb * 1024 ** 3)
    if need + reserve > free_bytes(work):
        raise StageError(f"Ổ đĩa máy chung không đủ chỗ: cần chép {_human(need)} (+ {cfg.work_reserve_gb:g} GB dự phòng), "
                         f"còn trống {_human(free_bytes(work))}. Nhờ admin dọn ổ rồi giao lại.")
    if on_start:
        on_start(len(todo), need)
    for f in todo:
        part = work / (f.name + ".part")
        try:
            shutil.copy2(f, part)
            part.replace(work / f.name)
        except OSError as e:
            part.unlink(missing_ok=True)
            raise StageError(f"Chép '{f.name}' lỗi ({e.__class__.__name__}): {e}") from e
    seeded = seed_transcripts(src, work, files)
    return StageResult(work, copied=len(todo), reused=len(files) - len(todo), total_bytes=total,
                       skipped_dirs=skipped_dirs, skipped_files=skipped_files, transcripts_reused=seeded)


def deliver_results(source_dir: Path, work_dir: Path, job_id: str, outputs: dict) -> Path:
    """Chép kết quả về `<footage gốc>\\edit\\<mã job>\\`. Thành công mới đánh dấu .delivered (cho phép dọn)."""
    dest = Path(source_dir) / "edit" / job_id
    dest.mkdir(parents=True, exist_ok=True)
    # gói chỉnh tay (videofac_*) đã nằm sẵn trên NAS và chỉ là file phục vụ người chỉnh: không chép lại thành "kết quả"
    wanted = [Path(v) for k, v in outputs.items() if isinstance(v, str) and not k.startswith("videofac_")]
    wanted += [Path(work_dir) / "edit" / n for n in RESULT_DOCS]
    for p in wanted:
        if p.is_file():
            shutil.copy2(p, dest / p.name)
    return_transcripts(Path(source_dir), Path(work_dir))   # job sau cùng footage khỏi gọi Scribe lại
    (Path(work_dir) / MARKER).write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return dest


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def cleanup_work(cfg: Config, jobs, now: datetime | None = None) -> list[str]:
    """Xóa thư mục làm việc của job đã xong/hủy quá `work_keep_hours` giờ. Trả về danh sách mã job đã dọn.

    Giữ lại: job đang chạy/chờ, job xong mà chưa có .delivered (kết quả chưa về NAS), thư mục không thuộc job nào.
    """
    now = now or datetime.now()
    root = cfg.work_path
    removed: list[str] = []
    if not root.is_dir():
        return removed
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        job = jobs.get(d.name)
        if not job or job["status"] not in ("done", "cancelled"):
            continue
        if job["status"] == "done" and not (d / MARKER).exists():
            continue
        t = _parse(job.get("updated_at"))
        if t is None or (now - t).total_seconds() < cfg.work_keep_hours * 3600:
            continue
        shutil.rmtree(d, ignore_errors=False)
        removed.append(d.name)
    return removed


def start_cleanup_thread(cfg: Config, jobs, every_s: int = 3600, log: Callable[[str], None] = print) -> threading.Thread:
    def loop() -> None:
        while True:
            try:
                n = cleanup_work(cfg, jobs)
                if n:
                    log(f"[dọn] đã xóa thư mục làm việc: {', '.join(n)}")
            except Exception as e:  # noqa: BLE001  (không để luồng dọn chết vì một lỗi)
                log(f"[dọn] lỗi: {e}")
            time.sleep(every_s)

    t = threading.Thread(target=loop, name="vidagents-cleanup", daemon=True)
    t.start()
    return t
