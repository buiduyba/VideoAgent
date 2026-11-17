"""Chạy thử toàn luồng qua HTTP: tạo run, đọc SSE, tự bấm duyệt khi được hỏi."""
import json
import sys
import urllib.request

B = "http://127.0.0.1:8000"
approve = (sys.argv[1] != "reject") if len(sys.argv) > 1 else True


def post(p, d):
    r = urllib.request.Request(B + p, json.dumps(d).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))


rid = post("/api/runs", {"brief": "Video 45 giây gian hàng hội chợ 20/11"})["run_id"]
print("run", rid)
sent = False
for line in urllib.request.urlopen(B + f"/api/runs/{rid}/events", timeout=180):
    line = line.decode("utf-8").strip()
    if not line.startswith("data:"):
        continue
    ev = json.loads(line[5:])
    if ev["type"] == "run":
        print("RUN ", ev["status"], ev["overall"])
        if ev["status"] == "waiting_approval" and not sent:
            sent = True
            print(post(f"/api/runs/{rid}/approve", {"approved": approve}))
    else:
        print(f'{ev["ts"]} {ev["agent"]:10} {ev["status"]:9} {ev["progress"]:3}% '
              f'ov={ev["overall"]:3} {ev["task"]} | {ev["log"]}')
