"""Gửi file qua Telegram lỗi (TimedOut khi mạng yếu) không được làm dừng job: kết quả đã nằm trên NAS, chỉ báo đường dẫn."""
import asyncio
import threading
from types import SimpleNamespace

from conftest import make_env
from vidagents.bot import TelegramNotifier


class FakeBot:
    def __init__(self, fail_docs):
        self.fail_docs, self.texts, self.docs = fail_docs, [], []

    async def send_message(self, chat_id, text):
        self.texts.append(text)

    async def send_document(self, chat_id, document, filename, **kw):
        self.docs.append((filename, kw))
        if self.fail_docs:
            raise TimeoutError("Timed out")


def notifier(tmp_path, fail_docs):
    e = make_env(tmp_path)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    n = TelegramNotifier(e.cfg)
    bot = FakeBot(fail_docs)
    n.attach(SimpleNamespace(bot=bot), loop)
    return n, bot, loop


def test_file_upload_timeout_does_not_raise_and_tells_where_the_file_is(tmp_path):
    n, bot, loop = notifier(tmp_path, fail_docs=True)
    f = tmp_path / "ket-qua.mp4"
    f.write_bytes(b"x" * 1000)
    n.send("1001", "XONG", [str(f)])                      # không được ném lỗi
    assert bot.texts[0] == "XONG"
    assert "không gửi được file ket-qua.mp4" in bot.texts[1] and str(f) in bot.texts[1] and "TimeoutError" in bot.texts[1]
    loop.call_soon_threadsafe(loop.stop)


def test_upload_uses_long_timeouts(tmp_path):
    n, bot, loop = notifier(tmp_path, fail_docs=False)
    f = tmp_path / "a.srt"
    f.write_bytes(b"x")
    n.send("1001", "ok", [str(f)])
    name, kw = bot.docs[0]
    assert name == "a.srt" and kw["write_timeout"] >= 300 and kw["read_timeout"] >= 300
    assert len(bot.texts) == 1                             # gửi được thì không có tin "không gửi được"
    loop.call_soon_threadsafe(loop.stop)
