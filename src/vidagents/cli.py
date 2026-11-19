from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from .config import Config


def load_env_file(path: Path) -> list[str]:
    """Nạp khóa từ file .env (KHÓA=giá trị, dòng # là ghi chú) vào biến môi trường của tiến trình.
    Biến đã đặt sẵn trong Windows thì giữ nguyên (biến của Windows thắng file). Trả danh sách TÊN đã nạp, không bao giờ in giá trị."""
    import os
    loaded = []
    try:
        lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return loaded
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and not os.environ.get(k):
            os.environ[k] = v
            loaded.append(k)
    return loaded


def _cfg(args) -> Config:
    return Config.load(args.config)


def cmd_doctor(args) -> int:
    print("\n".join(__import__("vidagents.doctor", fromlist=["doctor"]).doctor(_cfg(args))))
    return 0


def cmd_unlock(args) -> int:
    from .locks import SlotLock

    cfg = _cfg(args)
    n = SlotLock(cfg.locks_dir, cfg.render_slots).force_unlock()
    print(f"Đã gỡ {n} khóa render (chỉ dùng khi chắc chắn không còn job nào đang render).")
    return 0


def cmd_cleanup(args) -> int:
    from .jobs import JobStore
    from .workspace import cleanup_work

    cfg = _cfg(args)
    done = cleanup_work(cfg, JobStore(cfg.jobs_db))
    print(f"Đã dọn {len(done)} thư mục làm việc: {', '.join(done) or '(không có)'}")
    return 0


def cmd_cost(args) -> int:
    from .jobs import JobStore
    from .usage import UsageMeter

    cfg = _cfg(args)
    m = UsageMeter(cfg.jobs_db, cfg.pricing, cfg.max_cost_per_job_usd)
    rows = [m.job_total(args.job)] if args.job else m.totals_by_job()
    if not rows or not rows[0]["calls"]:
        print("Chưa có số liệu chi phí AI.")
        return 0
    jobs = JobStore(cfg.jobs_db)
    total = 0.0
    for t in rows:
        j = jobs.get(t["job_id"]) or {}
        cap = m.cap_for(t["job_id"])
        warn = f"  (chưa có giá cho {', '.join(t['unpriced_models'])})" if t["unpriced_models"] else ""
        print(f"{t['job_id']:<28} {j.get('status', '?'):<17} ${t['usd']:>7.2f}"
              f"{'/$%.2f' % cap if cap else '':<10} {t['input_tokens']:>10,} vào {t['output_tokens']:>9,} ra {t['calls']:>3} lượt{warn}")
        total += t["usd"]
    print(f"Tổng {len(rows)} job: ${total:.2f}. Giá theo bảng giá trong config (mặc định là số tham khảo, cần đối chiếu trang giá chính thức).")
    return 0


def cmd_watchdog(args) -> int:
    from .jobs import JobStore
    from .watchdog import check_stalled, http_notifier_from_env

    cfg = _cfg(args)
    n = http_notifier_from_env(cfg)
    done = check_stalled(cfg, JobStore(cfg.jobs_db), lambda uid, text: n.send(uid, text))
    print(f"Đã báo {len(done)} job đứng: {', '.join(done) or '(không có)'}")
    return 0


def cmd_bot(args) -> int:
    from .app import build_deps
    from .bot import TelegramNotifier, run_bot
    from .graph import build_graph, open_checkpointer, production_interrupt
    from .runner import JobRunner

    cfg = _cfg(args)
    notifier = TelegramNotifier(cfg)
    deps = build_deps(cfg, notifier=notifier, interrupt=production_interrupt())
    graph = build_graph(deps, open_checkpointer(cfg.checkpoint_db))
    admin = cfg.admin_ids[0] if cfg.admin_ids else None
    runner = JobRunner(graph, deps, admin_notify=(lambda t: notifier.send(admin, t)) if admin else None)
    from .workspace import start_cleanup_thread

    from .watchdog import start_watchdog_thread

    start_cleanup_thread(cfg, deps.jobs)  # dọn thư mục làm việc quá hạn mỗi giờ
    start_watchdog_thread(cfg, deps.jobs, notifier)  # báo khi video đứng (không bắt được lúc chính bot chết: dùng `watchdog` ngoài bot)
    run_bot(cfg, runner, deps, notifier)
    return 0


def demo_config(work: Path) -> Config:
    """Cấu hình của `vidagents demo` (mọi thứ nằm trong một thư mục). Dashboard dùng lại để xem dữ liệu demo."""
    here = Path(__file__).resolve().parents[2]
    work = Path(work)
    return Config(footage_root=work / "footage", projects_root=work / "projects", jobs_db=work / "jobs.sqlite",
                  checkpoint_db=work / "ckpt.sqlite", locks_dir=work / "locks", agents_dir=work / "agents",
                  video_types_dir=here / "video_types", skill_dir=work / "skill", render_slots=1, work_reserve_gb=0.0)


def cmd_dashboard(args) -> int:
    """Bảng theo dõi job trên trình duyệt. Chỉ đọc; mặc định chỉ nghe 127.0.0.1."""
    from .dashboard import Reader, make_server

    cfg = demo_config(args.demo_dir) if args.demo_dir else _cfg(args)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"CẢNH BÁO: đang nghe {args.host}, ai cùng mạng cũng xem được tên job, thương hiệu, chi phí (dashboard không có đăng nhập).")
    srv = make_server(Reader(cfg), args.host, args.port)
    print(f"Dashboard: http://{args.host}:{srv.server_address[1]}  (Ctrl+C để dừng)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def cmd_demo(args) -> int:
    """Chạy trọn một job bằng AI giả + công cụ giả qua LangGraph THẬT (kiểm chứng lớp nối, checkpointer, interrupt)."""
    from langgraph.types import Command

    from .app import build_deps
    from .fakes import DemoLLM, DemoTools
    from .graph import build_graph, open_checkpointer, pending_interrupts, production_interrupt

    work = Path(args.workdir or tempfile.mkdtemp(prefix="vidagents-demo-"))
    here = Path(__file__).resolve().parents[2]
    (work / "footage" / "clip-demo").mkdir(parents=True, exist_ok=True)
    (work / "footage" / "clip-demo" / "clip.mp4").write_bytes(b"demo")
    agents = work / "agents"
    if agents.exists():
        shutil.rmtree(agents)
    shutil.copytree(here / "agents", agents)
    cfg = demo_config(work)
    live = bool(getattr(args, "live", False))
    if live:  # Claude THẬT (tính tiền theo token), công cụ dựng vẫn là giả. Kiểm tra điều kiện TRƯỚC khi gọi bất cứ thứ gì.
        import os

        from .skills import AGENTS
        from .usage import price_usd

        if Path(args.config).exists():  # lấy bảng giá, model, tên biến chứa key từ config thật
            base = Config.load(args.config)
            cfg.pricing, cfg.models, cfg.default_model, cfg.anthropic_key_env = base.pricing, base.models, base.default_model, base.anthropic_key_env
        cfg.max_cost_per_job_usd = args.max_usd
        from .llm import has_ant_profile

        if not os.environ.get(cfg.anthropic_key_env) and not has_ant_profile():
            print(f"Chưa có thông tin đăng nhập Claude API. Chọn một cách: (1) chạy `ant auth login` (không cần key), hoặc "
                  f"(2) đặt biến môi trường {cfg.anthropic_key_env} trong cửa sổ dòng lệnh (không ghi key vào file hay chat). "
                  f"Chưa gọi Claude, chưa tốn đồng nào.")
            return 2
    (work / "skill").mkdir(exist_ok=True)
    deps = build_deps(cfg, llm=None if live else DemoLLM(heavy_rounds=1), tools_cls=DemoTools, interrupt=production_interrupt())
    if live:
        unpriced = sorted({cfg.model_for(a) for a in AGENTS if price_usd(deps.meter.pricing, cfg.model_for(a), 1000, 1000) is None})
        if unpriced:  # không biết giá thì trần chi phí không có tác dụng: từ chối chạy
            print(f"Model chưa có giá trong bảng giá: {', '.join(unpriced)}. Điền `pricing` trong config rồi chạy lại. Chưa gọi Claude.")
            return 2
        print(f"CHẠY THẬT: gọi Claude (model: {', '.join(sorted({cfg.model_for(a) for a in AGENTS}))}), trần chi phí ${args.max_usd:.2f}. "
              f"Công cụ dựng/soi khung vẫn là giả, nên chỉ kiểm chứng phần AI + luồng.")
    graph = build_graph(deps, open_checkpointer(cfg.checkpoint_db))
    uid, jid = "1001", "20261106-demo-01"
    deps.jobs.create(jid, requester_id=uid, requester_name="Demo", brand="demo", video_type="koc-review",
                     footage_dir=str(work / "footage" / "clip-demo"))
    config = {"configurable": {"thread_id": jid}, "recursion_limit": 100}
    inp = {"job_id": jid, "requester_id": uid, "requester_name": "Demo", "footage_dir": str(work / "footage" / "clip-demo"),
           "brand": "demo", "video_type": "koc-review",
           "brief_text": ("Video KOC review kem dưỡng da của thương hiệu demo, dài 30 giây. KOC tên Lan, đã đồng ý được quay và đăng. "
                          "Mục tiêu bán hàng, không có ưu đãi giá." if live else "Video KOC Sản phẩm demo"),
           "approvals": {}, "feedback": [], "steps": [], "human_revisions": 0, "critic_fix_rounds": 0, "info_rounds": 0, "outputs": {}}
    answers = {"ask_info": {"action": "answer", "by": uid, "note": "Mục tiêu: bán hàng. KOC đã đồng ý. Không có ưu đãi."},
               "approval": {"action": "approve", "by": uid}, "escalate": {"action": "continue", "by": uid},
               "lessons": {"action": "select", "by": uid, "ids": [1]}}
    from .usage import CostCapExceeded

    for turn in range(40):
        try:
            graph.invoke(inp, config)
        except CostCapExceeded as e:  # trần chi phí hoạt động: dừng, không gọi thêm
            print(f"DỪNG vì chạm trần chi phí: đã dùng ~${e.spent:.2f} / trần ${e.cap:.2f}")
            deps.jobs.update(jid, status="blocked", note="CostCapExceeded")
            break
        except Exception as e:  # noqa: BLE001 - chạy thật: lỗi API (hết credit, sai quyền...) in gọn, không đổ traceback
            if not live:
                raise
            msg = str(e)
            print(f"LỖI khi gọi Claude ({type(e).__name__}): {msg[:300]}")
            if "credit balance" in msg:
                print("→ Tổ chức chưa có credit. Nạp tiền ở Console > Plans & Billing rồi chạy lại (lỗi này chưa tính phí).")
            deps.jobs.update(jid, status="blocked", note=f"{type(e).__name__}: {msg[:200]}")
            break
        reqs, more = pending_interrupts(graph, config)
        if not reqs:
            break
        r = reqs[0]
        print(f"[lượt {turn}] chờ người: {r['kind']} → tự trả lời trong demo")
        inp = Command(resume=answers[r["kind"]])
    final = graph.get_state(config).values
    print("\nKẾT QUẢ:", final.get("status"), "| bước:", [s["name"] for s in final.get("steps", [])])
    if live:
        t = deps.meter.job_total(jid)
        print(f"CHI PHÍ AI: ${t['usd']:.4f} · {t['calls']} lượt · {t['input_tokens']:,} token vào · {t['output_tokens']:,} ra")
        for a, v in t["by_agent"].items():
            print(f"  - {a}: {v['calls']} lượt, ${v['usd']:.4f}")
        if t["unpriced_models"]:
            print("  (chưa có giá cho:", ", ".join(t["unpriced_models"]) + ")")
    print("Thư mục demo:", work)
    ok = final.get("status") == "done"
    print("DEMO", "THÀNH CÔNG" if ok else "CHƯA XONG (xem log phía trên)")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vidagents")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (("doctor", cmd_doctor, "kiểm tra cấu hình + lệnh skill đã khai báo"),
                               ("unlock", cmd_unlock, "gỡ khóa render bị kẹt"),
                               ("cleanup", cmd_cleanup, "dọn thư mục làm việc của job đã xong/hủy quá hạn"),
                               ("cost", cmd_cost, "xem chi phí AI theo job (thêm --job <mã> để xem một job)"),
                               ("watchdog", cmd_watchdog, "báo Telegram các video đứng quá hạn (chạy định kỳ ngoài bot, ví dụ mỗi 15 phút)"),
                               ("bot", cmd_bot, "chạy bot Telegram + worker")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--config", default="config.json")
        if name == "cost":
            p.add_argument("--job", default=None)
        p.set_defaults(fn=fn)
    p = sub.add_parser("dashboard", help="bảng theo dõi job trên trình duyệt (chỉ xem)")
    p.add_argument("--config", default="config.json")
    p.add_argument("--demo-dir", default=None, help="xem dữ liệu của một lần `vidagents demo --workdir <thư mục>`")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_dashboard)
    p = sub.add_parser("demo", help="chạy thử bằng AI giả + LangGraph thật")
    p.add_argument("--workdir", default=None)
    p.add_argument("--live", action="store_true", help="gọi Claude THẬT (tính tiền theo token; công cụ dựng vẫn giả)")
    p.add_argument("--max-usd", type=float, default=1.0, help="trần chi phí AI cho lần chạy --live (mặc định 1 USD)")
    p.add_argument("--config", default="config.json", help="chỉ dùng với --live: lấy bảng giá, model, tên biến chứa key")
    p.set_defaults(fn=cmd_demo)
    args = ap.parse_args(argv)
    # file .env cạnh config.json (hoặc ở thư mục đang chạy) để đặt khóa mà không cần sửa biến môi trường của Windows
    cfg_dir = Path(getattr(args, "config", "config.json")).resolve().parent
    for env in dict.fromkeys([cfg_dir / ".env", Path.cwd() / ".env"]):
        load_env_file(env)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
