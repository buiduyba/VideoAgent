"""Lệnh con của ToolRunner không được thừa kế bàn phím của bot: nếu chờ người gõ phím (vd. npx hỏi cài gói) cả job sẽ treo vô hạn."""
import sys

from vidagents.tools import ToolRunner


def test_child_process_has_no_stdin(env, tmp_path):
    runner = ToolRunner.__new__(ToolRunner)
    # chương trình con đọc stdin: nếu thừa kế bàn phím thì chờ mãi; với DEVNULL đọc được EOF ngay (chuỗi rỗng)
    code, out = runner._exec({"timeout": 20}, [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], tmp_path, {})
    assert code == 0 and out.strip() == "''"
