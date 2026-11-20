"""Tài nguyên của PROJECT: logo, ảnh sản phẩm, video AI trám, nằm cạnh thư mục ngày/sản phẩm trong Xưởng Video.

    Xưởng Video\\Nhật Hải\\            <- project (thư mục cấp 1 dưới footage_root)
        2026-11-20 Nước hoa\\          <- job: clip quay nằm ngay trong đây
        logo\\  anh-san-pham\\  video-ai\\    <- tài nguyên dùng chung cho mọi job của project

Tài nguyên cũng có thể đặt ngay trong thư mục job (ghi đè cho riêng job đó). Tên thư mục nhận theo bảng ALIASES (không phân biệt dấu,
hoa thường, dấu cách hay gạch nối). Thư mục khác trong project vẫn bị bỏ qua như trước.

Bot chép tài nguyên về `<thư mục làm việc>\\edit\\project-assets\\` (không nằm ở cấp trên cùng của footage nên KHÔNG bị nhận dạng lời,
KHÔNG tốn phí Scribe) và ghi bảng `edit\\project_assets.json` để các agent biết có gì để dùng:
- logo: tự gắn vào end card (EDL graphics.end.logo), không cần AI quyết;
- video AI: nguồn bổ sung cho cảnh phủ / cảnh chèn (thường không có tiếng, bước dựng của skill đã xử lý);
- ảnh sản phẩm: nền cho thẻ (gaps[i].image).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
VID_EXT = (".mp4", ".mov", ".mkv", ".m4v")
ALIASES = {
    "logo": ("logo", "logos"),
    "products": ("anh san pham", "san pham", "anh sp", "product", "products", "product images", "hinh san pham"),
    "ai": ("video ai", "ai video", "ai", "video tram", "b roll ai", "broll ai", "video ai tram"),
}
LIMITS = {"products": 24, "ai": 12}
SUBDIR = "project-assets"
MANIFEST = "project_assets.json"


def _key(name: str) -> str:
    s = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_ALIAS_KEYS = {k: {_key(a) for a in v} for k, v in ALIASES.items()}


def asset_kind(dirname: str) -> str | None:
    """'logo' / 'products' / 'ai' nếu tên thư mục là thư mục tài nguyên, không thì None (dùng để giấu khỏi danh sách chọn job)."""
    k = _key(dirname)
    return next((kind for kind, keys in _ALIAS_KEYS.items() if k in keys), None)


def project_dir(cfg: Config, footage_dir: str | Path) -> Path:
    """Project chứa thư mục job: thư mục cấp 1 dưới footage_root; job nằm ngay trong project hoặc ngoài vùng thì project = chính nó."""
    f, root = Path(footage_dir), Path(cfg.footage_root)
    try:
        parts = f.resolve().relative_to(root.resolve()).parts
    except (ValueError, OSError):
        try:
            parts = f.relative_to(root).parts
        except ValueError:
            return f
    return (root / parts[0]) if parts else f


@dataclass
class Assets:
    logo: Path | None = None
    products: list[Path] = field(default_factory=list)
    ai: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def any(self) -> bool:
        return bool(self.logo or self.products or self.ai)

    def summary(self) -> str:
        bits = []
        if self.logo:
            bits.append("logo")
        if self.products:
            bits.append(f"{len(self.products)} ảnh sản phẩm")
        if self.ai:
            bits.append(f"{len(self.ai)} video AI")
        return " · ".join(bits)


def _files(d: Path, exts: tuple[str, ...]) -> list[Path]:
    try:
        return sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() in exts and not p.name.startswith(".")), key=lambda p: p.name.casefold())
    except OSError:
        return []


def collect(cfg: Config, footage_dir: str | Path) -> Assets:
    """Tìm tài nguyên cho job: ở chính thư mục job trước (ghi đè), rồi ở thư mục project. Chưa chép gì."""
    jobd = Path(footage_dir)
    roots = [jobd]
    proj = project_dir(cfg, jobd)
    if proj != jobd:
        roots.append(proj)
    a = Assets()
    max_bytes = int(cfg.project_assets_max_mb * 1024 * 1024)
    ai_bytes = 0
    for root in roots:
        try:
            subs = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for d in subs:
            kind = asset_kind(d.name)
            if kind == "logo":
                imgs = _files(d, IMG_EXT)
                pick = next((p for p in imgs if _key(p.stem) == "logo"), imgs[0] if imgs else None)
                if pick and not a.logo:
                    a.logo = pick
                    if len(imgs) > 1:
                        a.notes.append(f"Thư mục logo có {len(imgs)} ảnh, dùng '{pick.name}' (ưu tiên file tên 'logo').")
            elif kind == "products":
                a.products += [p for p in _files(d, IMG_EXT) if p not in a.products]
            elif kind == "ai":
                for p in _files(d, VID_EXT):
                    size = p.stat().st_size
                    if p in a.ai:
                        continue
                    if len(a.ai) >= LIMITS["ai"] or ai_bytes + size > max_bytes:
                        a.notes.append(f"Bỏ qua video AI '{p.name}' (tối đa {LIMITS['ai']} clip / {cfg.project_assets_max_mb} MB).")
                        continue
                    a.ai.append(p)
                    ai_bytes += size
    if len(a.products) > LIMITS["products"]:
        a.notes.append(f"Có {len(a.products)} ảnh sản phẩm, chỉ dùng {LIMITS['products']} ảnh đầu (theo tên).")
        a.products = a.products[:LIMITS["products"]]
    return a


def _probe(cfg: Config, p: Path) -> dict:
    try:
        r = subprocess.run([cfg.ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(p)],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        info = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    out = {"w": v.get("width"), "h": v.get("height"), "has_audio": any(s.get("codec_type") == "audio" for s in info.get("streams", []))}
    try:
        out["duration_s"] = round(float((info.get("format") or {}).get("duration") or 0), 1)
    except (TypeError, ValueError):
        pass
    return out


def _unique(dest: Path, name: str) -> Path:
    p, k = dest / name, 2
    while p.exists():
        p = dest / f"{Path(name).stem} ({k}){Path(name).suffix}"
        k += 1
    return p


def stage(cfg: Config, assets: Assets, edit_dir: Path) -> dict:
    """Chép tài nguyên về `<edit_dir>\\project-assets\\{logo,products,ai}` và ghi bảng project_assets.json. Chạy lại an toàn (ghi lại từ đầu)."""
    base = Path(edit_dir) / SUBDIR
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    man: dict = {"ghi_chu": "Tài nguyên của project, đã chép về máy dựng. Đường dẫn dưới đây dùng trực tiếp trong EDL.", "logo": None,
                 "anh_san_pham": [], "video_ai": [], "luu_y": list(assets.notes)}
    if assets.logo:
        d = base / "logo"
        d.mkdir(parents=True, exist_ok=True)
        t = _unique(d, assets.logo.name)
        shutil.copy2(assets.logo, t)
        man["logo"] = t.as_posix()
    for p in assets.products:
        d = base / "products"
        d.mkdir(parents=True, exist_ok=True)
        t = _unique(d, p.name)
        shutil.copy2(p, t)
        man["anh_san_pham"].append({"ten": t.name, "path": t.as_posix(), **{k: v for k, v in _probe(cfg, t).items() if k in ("w", "h")}})
    for p in assets.ai:
        d = base / "ai"
        d.mkdir(parents=True, exist_ok=True)
        t = _unique(d, p.name)
        shutil.copy2(p, t)
        pr = _probe(cfg, t)
        man["video_ai"].append({"ten": t.name, "path": t.as_posix(), "giay": pr.get("duration_s"), "w": pr.get("w"), "h": pr.get("h"),
                                "co_tieng": pr.get("has_audio")})
    Path(edit_dir).mkdir(parents=True, exist_ok=True)
    (Path(edit_dir) / MANIFEST).write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return man


def load_manifest(edit_dir: str | Path) -> dict:
    try:
        return json.loads((Path(edit_dir) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def usage_hint(man: dict) -> str:
    """Dặn agent kịch bản cách dùng tài nguyên (chỉ khi có)."""
    if not (man.get("anh_san_pham") or man.get("video_ai")):
        return ""
    lines = ["\nTài nguyên của project (xem tai_nguyen_project). Dùng khi HỢP nội dung, không bắt buộc:"]
    if man.get("video_ai"):
        lines.append("• video_ai: clip trám do AI tạo (thường KHÔNG có tiếng, có thể ngắn). Thêm vào edl.sources bằng khóa mới với ĐÚNG đường dẫn đã cho, "
                     "rồi dùng làm cảnh phủ (covers) hoặc cảnh chèn (broll). KHÔNG dùng làm đoạn lời trong ranges.")
    if man.get("anh_san_pham"):
        lines.append("• anh_san_pham: ảnh sản phẩm. Dùng làm nền một thẻ: trong gaps thay source/start bằng \"image\": \"<đường dẫn ảnh>\" "
                     "(ảnh được đặt giữa khung, nền mờ, không cắt).")
    if man.get("logo"):
        lines.append("• logo: hệ thống tự gắn vào end card, bạn KHÔNG cần ghi gì.")
    return "\n".join(lines)
