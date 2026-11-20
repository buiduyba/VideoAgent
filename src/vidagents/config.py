"""Cấu hình. Đường dẫn tương đối được hiểu theo thư mục chứa file config."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _p(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return path if path.is_absolute() else (base / path)


EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _efforts(raw: dict) -> dict:
    """{"script": "medium", ...}: bỏ khóa bắt đầu bằng '_' (ghi chú); giá trị lạ thì báo ngay lúc nạp config, không đợi gọi API mới lỗi 400."""
    out = {}
    for k, v in (raw or {}).items():
        if str(k).startswith("_") or not v:
            continue
        if v not in EFFORTS:
            raise ValueError(f"config.efforts['{k}'] = {v!r}: chỉ nhận {', '.join(EFFORTS)}")
        out[k] = v
    return out


@dataclass
class Config:
    footage_root: Path
    projects_root: Path
    jobs_db: Path
    checkpoint_db: Path
    locks_dir: Path
    agents_dir: Path
    video_types_dir: Path
    skill_dir: Path | None = None  # thư mục skill dựng video hiện có, dùng cho biến {SKILL}
    render_slots: int = 1  # số "suất nặng" chạy cùng lúc (cắt 4K, render)
    lock_timeout_s: int = 6 * 3600
    lock_stale_s: int = 4 * 3600
    max_critic_rounds: int = 2  # nguyên tắc 5
    max_revisions: int = 5  # số lần người duyệt được yêu cầu sửa trước khi hỏi lại
    max_info_rounds: int = 3
    light_workers: int = 4  # số job chạy bước NHẸ cùng lúc (job đang chờ/chạy bước nặng không chiếm chỗ này)
    job_threads: int = 64  # tổng số luồng của bộ chạy job: gồm cả luồng đang chờ suất render nên phải lớn hơn light_workers nhiều
    default_model: str = "claude-sonnet-5-5"
    models: dict = field(default_factory=dict)  # {"script": "...", "critic": "..."}
    # Mức "suy nghĩ" của model theo từng agent: low | medium | high | xhigh | max. Chữ AI viết ra (kể cả suy nghĩ) chiếm ~72% chi phí,
    # nên đây là cần gạt lớn nhất. Agent không khai báo thì dùng mặc định của model (Sonnet 5.5: high).
    efforts: dict = field(default_factory=dict)
    # Hỏi đáp tự do trong chat (chat.py): hạn mức USD mỗi người mỗi ngày (None = không giới hạn). Model: models.chat, mặc định Claude Haiku 5.5.
    chat_daily_usd: float | None = 0.30
    # Tài nguyên project (logo, ảnh sản phẩm, video AI): tổng dung lượng video AI tối đa chép về mỗi job (MB)
    project_assets_max_mb: int = 1500
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    # Nhánh chỉnh tay: đường dẫn import.js của VideoFac (repo tool-dung-video) + lệnh Node. None = không có nhánh chỉnh tay.
    videofac_import: Path | None = None
    node_exe: str = "node"
    anthropic_key_env: str = "ANTHROPIC_API_KEY"
    telegram_token_env: str = "TELEGRAM_BOT_TOKEN"
    allowed_user_ids: list = field(default_factory=list)
    user_names: dict = field(default_factory=dict)  # {"<ID Telegram>": "tên gọi"}: chỉ để hiển thị trong /danhsach
    source_path: Path | None = None  # file config đã nạp (để /themnguoi ghi lại)
    admin_ids: list = field(default_factory=list)  # chỉ được hủy job, không được duyệt hộ
    max_file_send_mb: int = 45
    # Thư mục làm việc riêng từng job (nên là ổ nội bộ của máy chung, không phải NAS)
    work_root: Path | None = None  # mặc định: <projects_root>/_work
    work_keep_hours: float = 24.0  # giữ bản chép bao lâu sau khi job xong/hủy
    work_copy_exts: tuple = (".mp4", ".mov", ".mkv", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav", ".m4a")
    work_reserve_gb: float = 5.0  # ổ đĩa phải còn trống thêm ngần này sau khi chép
    # Chi phí AI: pricing = {"<model>": {"in":..,"out":..,"cache_read":..,"cache_write":..}} (USD / 1 triệu token), ghi đè giá mặc định
    pricing: dict = field(default_factory=dict)
    max_cost_per_job_usd: float | None = None  # None = chưa đặt trần (nên đặt); đổi riêng từng job bằng /nangtran
    # Cảnh báo job đứng: job ở trạng thái chạy mà không đổi bước quá ngần này phút thì báo người giao + quản trị
    stall_minutes: int = 60
    watchdog_every_s: int = 300
    # Bản xem trước nhẹ (adapter adapters/preview_b.py). Giá trị --quality của HyperFrames CHƯA kiểm chứng: để trống = không gửi cờ (render đủ chất lượng); xem `hyperframes render --help` rồi điền
    preview_quality: str = ""
    # Cổng chi phí nhận dạng lời (ElevenLabs Scribe, tính tiền theo phút âm thanh). Số phút cần gọi Scribe <= ngưỡng thì tự
    # chạy; lớn hơn (hoặc không đo được độ dài) thì hỏi người giao qua Telegram. None = luôn hỏi khi có phút nào phải gọi.
    scribe_auto_max_minutes: float | None = None

    @property
    def adapters_dir(self) -> Path:
        return Path(__file__).resolve().parents[2] / "adapters"

    @property
    def work_path(self) -> Path:
        return self.work_root or (self.projects_root / "_work")

    def model_for(self, agent: str) -> str:
        return self.models.get(agent) or self.default_model

    def effort_for(self, agent: str) -> str | None:
        return self.efforts.get(agent) or self.efforts.get("default") or None

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path).resolve()
        raw = json.loads(path.read_text(encoding="utf-8-sig"))   # -sig: chịu được dấu BOM (Notepad và PowerShell hay tự thêm khi lưu)
        base = path.parent
        tg = raw.get("telegram", {})
        return cls(
            footage_root=_p(base, raw["footage_root"]),
            projects_root=_p(base, raw["projects_root"]),
            jobs_db=_p(base, raw["jobs_db"]),
            checkpoint_db=_p(base, raw["checkpoint_db"]),
            locks_dir=_p(base, raw["locks_dir"]),
            agents_dir=_p(base, raw.get("agents_dir", "agents")),
            video_types_dir=_p(base, raw.get("video_types_dir", "video_types")),
            skill_dir=_p(base, raw.get("skill_dir")),
            render_slots=int(raw.get("render_slots", 1)),
            lock_timeout_s=int(raw.get("lock_timeout_s", 6 * 3600)),
            lock_stale_s=int(raw.get("lock_stale_s", 4 * 3600)),
            max_critic_rounds=int(raw.get("max_critic_rounds", 2)),
            max_revisions=int(raw.get("max_revisions", 5)),
            max_info_rounds=int(raw.get("max_info_rounds", 3)),
            light_workers=int(raw.get("light_workers", 4)),
            job_threads=int(raw.get("job_threads", 64)),
            default_model=raw.get("models", {}).get("default", "claude-sonnet-5-5"),
            models={k: v for k, v in raw.get("models", {}).items() if k != "default" and v},
            efforts=_efforts(raw.get("efforts", {})),
            project_assets_max_mb=int(raw.get("project_assets_max_mb", 1500)),
            chat_daily_usd=(None if raw.get("chat_daily_usd", 0.30) is None else float(raw.get("chat_daily_usd", 0.30))),
            videofac_import=_p(base, raw.get("videofac_import")),
            node_exe=raw.get("node", "node"),
            ffmpeg=raw.get("ffmpeg", "ffmpeg"),
            ffprobe=raw.get("ffprobe", "ffprobe"),
            anthropic_key_env=raw.get("anthropic_key_env", "ANTHROPIC_API_KEY"),
            telegram_token_env=tg.get("token_env", "TELEGRAM_BOT_TOKEN"),
            allowed_user_ids=[str(x) for x in tg.get("allowed_user_ids", [])],
            user_names={str(k): str(v) for k, v in (tg.get("user_names") or {}).items()},
            source_path=path,
            admin_ids=[str(x) for x in tg.get("admin_ids", [])],
            max_file_send_mb=int(tg.get("max_file_send_mb", 45)),
            work_root=_p(base, raw.get("work_root")),
            work_keep_hours=float(raw.get("work_keep_hours", 24)),
            work_copy_exts=tuple(e.lower() for e in raw.get("work_copy_exts", cls.work_copy_exts)),
            work_reserve_gb=float(raw.get("work_reserve_gb", 5)),
            pricing=raw.get("pricing") or {},
            max_cost_per_job_usd=(float(raw["max_cost_per_job_usd"]) if raw.get("max_cost_per_job_usd") else None),
            stall_minutes=int(raw.get("stall_minutes", 60)),
            watchdog_every_s=int(raw.get("watchdog_every_s", 300)),
            preview_quality=str(raw.get("preview_quality", "")),
            scribe_auto_max_minutes=(float(raw["scribe_auto_max_minutes"]) if raw.get("scribe_auto_max_minutes") is not None else None),
        )
