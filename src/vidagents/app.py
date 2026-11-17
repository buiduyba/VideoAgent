from __future__ import annotations

from .config import Config
from .jobs import JobStore
from .llm import ClaudeLLM
from .locks import SlotLock
from .nodes import Deps
from .notify import ConsoleNotifier
from .schemas import load_video_types
from .skills import SkillLoader
from .tools import ToolRunner
from .usage import UsageMeter


def build_deps(cfg: Config, *, llm=None, notifier=None, tools_cls=ToolRunner, interrupt=None) -> Deps:
    locks = SlotLock(cfg.locks_dir, cfg.render_slots, cfg.lock_stale_s)
    meter = UsageMeter(cfg.jobs_db, cfg.pricing, cfg.max_cost_per_job_usd)
    return Deps(
        cfg=cfg, llm=llm or ClaudeLLM(cfg, meter), tools=tools_cls(cfg, locks), notifier=notifier or ConsoleNotifier(),
        jobs=JobStore(cfg.jobs_db), skills=SkillLoader(cfg), types=load_video_types(cfg.video_types_dir),
        interrupt=interrupt or (lambda v: (_ for _ in ()).throw(RuntimeError("chưa gắn interrupt"))),
        meter=meter,
    )
