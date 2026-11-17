"""Gọi Claude qua API (trả phí theo token, tính riêng với gói Claude đang dùng). Đầu ra luôn ép theo schema."""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Protocol, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Config
from .schemas import SchemaError

T = TypeVar("T", bound=BaseModel)


def has_ant_profile() -> bool:
    """Đã có đăng nhập `ant auth login` trên máy chưa (chỉ nhìn file, không gọi mạng). Thư mục theo tài liệu Anthropic:
    $ANTHROPIC_CONFIG_DIR, hoặc %APPDATA%\\Anthropic (Windows), hoặc ~/.config/anthropic."""
    d = os.environ.get("ANTHROPIC_CONFIG_DIR")
    if not d:
        d = os.path.join(os.environ["APPDATA"], "Anthropic") if os.name == "nt" and os.environ.get("APPDATA") \
            else os.path.expanduser("~/.config/anthropic")
    return any((Path(d) / "credentials").glob("*.json"))


class LLM(Protocol):
    def structured(self, *, agent: str, system: str, user: str, schema: type[T], images: Sequence[Path] = ()) -> T: ...


class ClaudeLLM:
    TOOL = "tra_ket_qua"

    def __init__(self, cfg: Config, meter=None):
        self.cfg = cfg
        self.meter = meter  # UsageMeter: ghi token + tiền theo job, kiểm trần trước mỗi lần gọi
        self._client = None
        self.usage: dict[str, list[int]] = {}  # agent -> [token vào, token ra] (cộng dồn cả tiến trình)

    def _c(self):
        if self._client is None:
            import anthropic  # nạp muộn để test không cần cài

            key = os.environ.get(self.cfg.anthropic_key_env)
            try:
                # Có key trong biến môi trường thì dùng key. Không có thì để SDK tự tìm đăng nhập `ant auth login` (profile) hoặc
                # liên minh danh tính (không cần giữ key). Thứ tự ưu tiên của SDK: key > ANTHROPIC_PROFILE > liên minh > profile đang dùng.
                self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
            except Exception as e:  # noqa: BLE001
                raise SchemaError(f"Không có thông tin đăng nhập Claude API: đặt {self.cfg.anthropic_key_env} "
                                  f"hoặc chạy `ant auth login` ({type(e).__name__}: {str(e)[:200]})") from e
        return self._client

    @staticmethod
    def _image_block(path: Path) -> dict:
        media = {"png": "image/png", "webp": "image/webp"}.get(path.suffix.lower().lstrip("."), "image/jpeg")
        return {"type": "image", "source": {"type": "base64", "media_type": media,
                                            "data": base64.b64encode(path.read_bytes()).decode()}}

    def structured(self, *, agent: str, system: str, user: str, schema: type[T], images: Sequence[Path] = ()) -> T:
        tool = {"name": self.TOOL, "description": "Trả kết quả đúng khuôn yêu cầu.",
                "input_schema": schema.model_json_schema()}
        err = ""
        for _ in range(2):
            content = [self._image_block(Path(p)) for p in images] + [{"type": "text", "text": user + err}]
            if self.meter:
                self.meter.check()  # chạm trần chi phí của job thì ném CostCapExceeded, không gọi tiếp
            model = self.cfg.model_for(agent)
            resp = self._c().messages.create(
                model=model, max_tokens=8000, system=system,
                messages=[{"role": "user", "content": content}], tools=[tool],
                tool_choice={"type": "tool", "name": self.TOOL},
            )
            u = self.usage.setdefault(agent, [0, 0])
            u[0] += getattr(resp.usage, "input_tokens", 0)
            u[1] += getattr(resp.usage, "output_tokens", 0)
            if self.meter:
                self.meter.record(agent, model, getattr(resp.usage, "input_tokens", 0) or 0, getattr(resp.usage, "output_tokens", 0) or 0,
                                  getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                                  getattr(resp.usage, "cache_creation_input_tokens", 0) or 0)
            block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
            if block is None:
                err = "\n\nLần trước không trả kết quả theo khuôn. Hãy gọi công cụ tra_ket_qua."
                continue
            try:
                return schema.model_validate(block.input)
            except ValidationError as e:
                err = f"\n\nLần trước sai khuôn: {str(e)[:500]}. Trả lại đúng khuôn."
        raise SchemaError(f"Agent '{agent}' không trả được kết quả đúng khuôn {schema.__name__}")
