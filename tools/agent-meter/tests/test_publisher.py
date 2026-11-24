#!/usr/bin/env python3
"""Offline checks for tools/agent-meter/agent_meter.py: no GitHub, no Claude, no systemd.

Runs the module under a throwaway HOME with gh, the Claude reader and the
service install replaced by fakes, and checks what would be written and when.
usage: python3 -I tests/test_publisher.py
"""
import importlib.util, json, os, shutil, stat, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail}" if detail and not ok else ""))


home = tempfile.mkdtemp(prefix="agent-meter-test-")
os.environ["HOME"] = home
spec = importlib.util.spec_from_file_location("agent_meter", ROOT / "agent_meter.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
check("paths live under the test HOME", str(m.CONF_FILE).startswith(home))

calls = []
GIST = "fedcba9876543210fedcba9876543210"


def fake_gh(args, body=None, timeout=40):
    calls.append((args, json.loads(body) if body else None))
    if args[:4] == ["api", "--method", "POST", "/gists"]:
        return json.dumps({"id": GIST, "owner": "Expatrocious"})
    if args[:3] == ["api", "--method", "PATCH"]:
        if fail_next[0]:
            fail_next[0] = False
            raise RuntimeError(f"gh: HTTP 502 for https://api.github.com/gists/{GIST}")
        return '"2026-11-23T12:00:00Z"\n'
    if args[:3] == ["api", "--method", "DELETE"]:
        return ""
    raise AssertionError(args)


fail_next = [False]
m.gh = fake_gh
m.install_service = lambda: None
reading = {"plan": "pro", "windows": [
    {"id": "five_hour", "label": "session", "pct": 40.0, "resets_at": "2026-11-23T13:30:00Z", "period_s": 18000},
    {"id": "seven_day", "label": "week", "pct": 61.0, "resets_at": "2026-11-24T01:00:00Z", "period_s": 604800}],
    "extra": None}
broken = [False]


def fake_read():
    if broken[0]:
        raise RuntimeError("claude gave no usage: offline")
    return json.loads(json.dumps(reading))


m.AGENTS[0].read = fake_read
m.AGENTS[0].status = lambda: "active"

# setup: creates a secret gist with the first reading, saves config 0600, writes the QR
m.setup()
conf = json.loads(m.CONF_FILE.read_text())
created = [c for c in calls if c[0][:4] == ["api", "--method", "POST", "/gists"]]
check("setup creates one gist", len(created) == 1)
body = created[0][1]
check("the gist is secret", body["public"] is False)
doc = json.loads(body["files"]["usage.json"]["content"])
check("gist holds the usage document", doc["v"] == 2 and doc["agents"][0]["windows"][0]["pct"] == 40.0)
check("no account details in it", set(doc["agents"][0]) <= {"id", "name", "plan", "windows", "extra", "updated_at", "error", "server"})
check("config has gist, owner, pages", conf == {"gist": GIST, "owner": "Expatrocious", "pages": m.PAGES}, str(conf))
check("config is private (0600)", stat.S_IMODE(os.stat(m.CONF_FILE).st_mode) == 0o600)
check("QR written, private", m.QR_FILE.exists() and stat.S_IMODE(os.stat(m.QR_FILE).st_mode) == 0o600)
payload = json.loads(m.qr_payload(conf))
check("QR payload is rabbit's creation format",
      set(payload) == {"title", "url", "description", "iconUrl", "themeColor"}
      and payload["url"] == f"{m.PAGES}?g={GIST}&u=Expatrocious" and payload["iconUrl"] == m.PAGES + "icon.png", str(payload))
calls.clear()
m.setup()
check("second setup makes no new gist", not [c for c in calls if c[0][3:4] == ["/gists"]])

# publish timing
pub = m.Publisher(conf)
pushes = lambda: len([c for c in calls if c[0][:3] == ["api", "--method", "PATCH"]])
calls.clear()
check("first step publishes", pub.step(1000.0) and pushes() == 1)
check("nothing new 2 min later: no write", not pub.step(1120.0) and pushes() == 1)
check("reads wait for the 2-minute timer", not pub.step(1130.0))
reading["windows"][0]["pct"] = 40.4
check("a change under 1% is not news", not pub.step(1240.0) and pushes() == 1)
reading["windows"][0]["pct"] = 41.6
check("a whole-percent change is published", pub.step(1360.0) and pushes() == 2)
check("heartbeat after 10 minutes with no change", not pub.step(1480.0) and pub.step(1960.0) and pushes() == 3)
broken[0] = True
check("a failed read is published once (the r1 shows it), keeping the numbers", pub.step(2080.0) and pushes() == 4)
last = json.loads(calls[-1][1]["files"]["usage.json"]["content"])["agents"][0]
check("... with the error and the last good numbers", last["error"] and last["windows"][0]["pct"] == 41.6, str(last)[:120])
check("after a failed read, retry in 60 s", pub.next_read == 2140.0, str(pub.next_read))
broken[0] = False
fail_next[0] = True
errs = []
m.log = errs.append
check("a failed write is retried", not pub.step(2140.0) and pub.next_read == 2200.0 and pub.step(2200.0))
check("logged errors never show the gist id", errs and all(GIST not in e for e in errs), str(errs))

# rotate: new gist, old one deleted
calls.clear()
GIST_OLD = GIST
GIST = "00112233445566778899aabbccddeeff"
m.rotate()
conf2 = json.loads(m.CONF_FILE.read_text())
check("rotate saves the new gist", conf2["gist"] == GIST)
check("rotate deletes the old gist", any(c[0][:4] == ["api", "--method", "DELETE", f"/gists/{GIST_OLD}"] for c in calls))

shutil.rmtree(home, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
