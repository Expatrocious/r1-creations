#!/usr/bin/env python3
"""agent meter: AI agent usage on the rabbit r1, published through GitHub.

The r1 creation is a static page on GitHub Pages (agent-meter/ in r1-creations). This
program runs on the PC. Every two minutes it reads each agent's usage and, when
a number changed (or every 10 minutes, as a heartbeat), writes it to a secret
gist that the creation reads. Nothing on the PC is opened to the internet.

What leaves the PC: plan name, usage percentages, reset times, and whether the
phone-sessions server is running. No account details, no tokens. GitHub access
is the gh CLI's own login; this program never sees the token.

  agent-meter run       publish loop (agent-meter.service runs this)
  agent-meter setup     create the secret gist (once), start the service, write the QR
  agent-meter push      read and publish once, now
  agent-meter usage     print the usage JSON (publishes nothing)
  agent-meter url       print the creation's address (it contains the gist id)
  agent-meter qr        write the install QR to ~/Pictures/agent-meter-qr.png
  agent-meter qr-text   print what the QR holds (for a terminal QR: qrencode -t ANSIUTF8)
  agent-meter rotate    new secret gist; old QR codes stop working

Agents (add more to AGENTS at the bottom):
  claude   plan limits (5-hour session, week, per-model weeks, extra usage),
           read through Claude Code itself (`claude -p`, control request
           get_usage: no prompt, no model call, no login handling here).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CONF_DIR = HOME / ".config/agent-meter"
CONF_FILE = CONF_DIR / "config.json"          # {"gist", "owner", "pages"}; 0600
STATE_DIR = HOME / ".local/state/agent-meter"
LAST_FILE = STATE_DIR / "last.json"
QR_FILE = HOME / "Pictures/agent-meter-qr.png"
GIST_FILE = "usage.json"
PAGES = "https://expatrocious.github.io/r1-creations/agent-meter/"

READ_EVERY = 120        # seconds between usage reads
HEARTBEAT = 600         # publish at least this often, so the r1 can tell the PC is up
RETRY = 60              # after a failed read or push


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def child_env():
    env = dict(os.environ)
    extra = [str(HOME / ".local/share/mise/command-wrappers/bin"), str(HOME / ".local/share/mise/shims"),
             str(HOME / ".local/bin"), "/usr/local/bin", "/usr/bin"]
    env["PATH"] = ":".join(extra + [p for p in env.get("PATH", "").split(":") if p and p not in extra])
    env.setdefault("GH_PROMPT_DISABLED", "1")
    env.setdefault("GH_NO_UPDATE_NOTIFIER", "1")
    return env


def exe(name, *candidates):
    for path in candidates:
        if os.access(path, os.X_OK):
            return str(path)
    return shutil.which(name, path=child_env()["PATH"]) or name


# ---------------------------------------------------------------- config
def load_conf():
    try:
        conf = json.loads(CONF_FILE.read_text())
        return conf if isinstance(conf, dict) else {}
    except (OSError, ValueError):
        return {}


def save_conf(conf):
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONF_DIR, 0o700)
    tmp = CONF_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(conf, f, indent=1)
        f.write("\n")
    os.replace(tmp, CONF_FILE)


def creation_url(conf=None):
    conf = conf or load_conf()
    if not conf.get("gist"):
        return None
    return f"{conf.get('pages') or PAGES}?g={conf['gist']}&u={conf.get('owner', '')}"


def scrub(text, conf=None):
    """Error text without the gist id (it is the creation's only secret)."""
    gist = (conf or load_conf()).get("gist")
    text = str(text)
    return text.replace(gist, "<gist>") if gist else text


# ---------------------------------------------------------------- github (through gh)
def gh(args, body=None, timeout=40):
    gh_bin = exe("gh", HOME / ".local/share/mise/shims/gh")
    p = subprocess.run([gh_bin] + args, input=body, capture_output=True, text=True,
                       timeout=timeout, env=child_env())
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-1:]
        raise RuntimeError(("gh: " + (tail[0] if tail else f"exit {p.returncode}"))[:200])
    return p.stdout


def gist_create(content):
    body = json.dumps({"description": "agent meter: usage for the r1 creation (written by the PC)",
                       "public": False, "files": {GIST_FILE: {"content": content}}})
    out = gh(["api", "--method", "POST", "/gists", "--input", "-", "--jq", "{id: .id, owner: .owner.login}"], body)
    made = json.loads(out)
    return made["id"], made["owner"]


def gist_write(gist, content):
    body = json.dumps({"files": {GIST_FILE: {"content": content}}})
    gh(["api", "--method", "PATCH", f"/gists/{gist}", "--input", "-", "--jq", ".updated_at"], body)


def gist_delete(gist):
    gh(["api", "--method", "DELETE", f"/gists/{gist}"])


# ---------------------------------------------------------------- claude
CLAUDE_WINDOWS = [("five_hour", "session", 5 * 3600), ("seven_day", "week", 7 * 86400),
                  ("seven_day_opus", "opus week", 7 * 86400), ("seven_day_sonnet", "sonnet week", 7 * 86400),
                  ("seven_day_oauth_apps", "apps week", 7 * 86400)]


def read_claude():
    """Plan limits through Claude Code's own /usage request (no prompt, no model call)."""
    request = {"type": "control_request", "request_id": "agent-meter",
               "request": {"subtype": "get_usage", "skip_behaviors": True}}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    claude = exe("claude", HOME / ".local/share/mise/command-wrappers/bin/claude",
                 HOME / ".local/share/mise/installs/claude/latest/claude", HOME / ".local/bin/claude")
    proc = subprocess.run(
        [claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
         "--no-session-persistence", "--setting-sources", ""],
        input=json.dumps(request) + "\n", capture_output=True, text=True, timeout=60,
        cwd=STATE_DIR, env=child_env())
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") != "control_response":
            continue
        resp = msg.get("response") or {}
        if resp.get("subtype") == "success":
            return claude_summary(resp.get("response") or {})
        raise RuntimeError(str(resp.get("error") or "claude refused the usage request")[:160])
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
    raise RuntimeError(("claude gave no usage: " + (tail[0] if tail else f"exit {proc.returncode}"))[:160])


def when(value):
    """Reset times as plain UTC seconds ('...T13:30:00Z'), whatever Claude Code sent."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return iso(round(dt.timestamp()))
    except (TypeError, ValueError):
        return None


def claude_summary(usage):
    limits = usage.get("rate_limits") or {}
    windows = []
    for key, label, period in CLAUDE_WINDOWS:
        w = limits.get(key)
        if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
            windows.append({"id": key, "label": label, "pct": round(float(w["utilization"]), 1),
                            "resets_at": when(w.get("resets_at")), "period_s": period})
    for w in limits.get("model_scoped") or []:
        if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
            name = str(w.get("display_name") or "model")[:20]
            windows.append({"id": "model:" + name, "label": name.lower() + " week",
                            "pct": round(float(w["utilization"]), 1), "resets_at": when(w.get("resets_at")),
                            "period_s": 7 * 86400})
    extra = limits.get("extra_usage") if isinstance(limits.get("extra_usage"), dict) else {}
    return {
        "plan": usage.get("subscription_type"),
        "windows": windows,
        "extra": ({"pct": extra.get("utilization"), "used": extra.get("used_credits"),
                   "limit": extra.get("monthly_limit"), "currency": extra.get("currency"),
                   "decimals": extra.get("decimal_places")} if extra.get("is_enabled") else None),
    }


def claude_server():
    """Is the always-on Remote Control server (start sessions from the phone) running?"""
    try:
        out = subprocess.run(["systemctl", "--user", "is-active", "claude-remote-control.service"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return out or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ---------------------------------------------------------------- agents
class Agent:
    def __init__(self, ident, name, read, status=None):
        self.id, self.name, self.read, self.status = ident, name, read, status
        self.data, self.at, self.error = None, 0.0, None

    def refresh(self):
        try:
            self.data, self.at, self.error = self.read(), time.time(), None
        except Exception as e:                  # keep the last good reading, say why it is old
            self.error = (str(e) or type(e).__name__)[:160]
            log(f"{self.id}: {self.error}")

    def snapshot(self):
        out = {"id": self.id, "name": self.name}
        out.update(self.data or {"windows": []})
        out["updated_at"] = iso(self.at) if self.at else None
        out["error"] = self.error
        if self.status:
            out["server"] = self.status()
        return out


def save_last():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = LAST_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({a.id: {"data": a.data, "at": a.at} for a in AGENTS if a.data}))
        os.replace(tmp, LAST_FILE)
    except OSError:
        pass


def load_last():
    try:
        saved = json.loads(LAST_FILE.read_text())
    except (OSError, ValueError):
        return
    for a in AGENTS:
        entry = saved.get(a.id)
        if isinstance(entry, dict) and isinstance(entry.get("data"), dict) and "windows" in entry["data"]:
            a.data, a.at = entry["data"], float(entry.get("at") or 0)


def usage():
    return {"v": 2, "published_at": iso(time.time()), "agents": [a.snapshot() for a in AGENTS]}


def fingerprint(doc):
    """What the r1 would show differently: whole percents, resets, plan, errors, server."""
    view = []
    for a in doc["agents"]:
        view.append([a.get("plan"), a.get("server"), bool(a.get("error")),
                     [(w.get("id"), round(w.get("pct") or 0), w.get("resets_at")) for w in a.get("windows", [])],
                     round((a.get("extra") or {}).get("pct") or 0)])
    return json.dumps(view)


def read_all():
    for a in AGENTS:
        a.refresh()
    save_last()
    return usage()


# ---------------------------------------------------------------- commands
def publish(doc, conf):
    gist_write(conf["gist"], json.dumps(doc, separators=(",", ":")))


class Publisher:
    """Reads on a timer; writes the gist when the r1 would show something new."""

    def __init__(self, conf):
        self.conf = conf
        self.last_print, self.last_push, self.next_read = None, 0.0, 0.0

    def step(self, now):
        if now < self.next_read:
            return False
        doc = read_all()
        fp = fingerprint(doc)
        self.next_read = now + (READ_EVERY if all(not a.error for a in AGENTS) else RETRY)
        if fp == self.last_print and now - self.last_push < HEARTBEAT:
            return False
        try:
            publish(doc, self.conf)
        except Exception as e:
            log("publish failed: " + scrub(e, self.conf))
            self.next_read = now + RETRY
            return False
        self.last_print, self.last_push = fp, now
        return True


def run():
    conf = load_conf()
    if not conf.get("gist"):
        log("no gist yet: run `agent-meter setup` once")
        sys.exit(2)
    load_last()
    pub = Publisher(conf)
    log(f"publishing every change (reads every {READ_EVERY}s, heartbeat {HEARTBEAT}s)")
    while True:
        pub.step(time.time())
        time.sleep(5)


def setup():
    conf = load_conf()
    if conf.get("gist"):
        print("already set up; `agent-meter rotate` makes a new gist")
    else:
        doc = read_all()
        gist, owner = gist_create(json.dumps(doc, separators=(",", ":")))
        conf.update({"gist": gist, "owner": owner, "pages": PAGES})
        save_conf(conf)
        print(f"secret gist created for {owner}")
    install_service()
    write_qr(conf)


def install_service():
    """Install and (re)start agent-meter.service from this repo's copy."""
    unit = Path(__file__).resolve().parent / "agent-meter.service"
    dest = HOME / ".config/systemd/user/agent-meter.service"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.read_text() != unit.read_text():
        dest.write_text(unit.read_text())
    sc = ["systemctl", "--user"]
    subprocess.run(sc + ["daemon-reload"], check=True)
    subprocess.run(sc + ["enable", "agent-meter.service"], check=True, capture_output=True)
    subprocess.run(sc + ["restart", "agent-meter.service"], check=True)
    print("agent-meter.service is running (publishes every change)")


def rotate():
    conf = load_conf()
    old = conf.get("gist")
    doc = read_all()
    gist, owner = gist_create(json.dumps(doc, separators=(",", ":")))
    conf.update({"gist": gist, "owner": owner, "pages": conf.get("pages") or PAGES})
    save_conf(conf)
    if old:
        try:
            gist_delete(old)
        except Exception as e:
            print("could not delete the old gist: " + scrub(e).replace(old, "<old gist>"))
    install_service()
    write_qr(conf)
    print("new secret gist; scan the new QR on the r1 (the old one has stopped updating)")


def qr_payload(conf):
    """What the r1 reads from the install QR (rabbit's creation format)."""
    pages = conf.get("pages") or PAGES
    return json.dumps({"title": "agent meter", "url": creation_url(conf),
                       "description": "Claude usage at a glance",
                       "iconUrl": pages + "icon.png", "themeColor": "#ff5300"}, separators=(",", ":"))


def write_qr(conf):
    payload = qr_payload(conf)
    QR_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QR_FILE.with_name(".agent-meter-qr.tmp.png")
    subprocess.run(["qrencode", "-o", str(tmp), "-s", "10", "-m", "3", "-l", "M", payload], check=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, QR_FILE)
    print(f"QR written to {QR_FILE}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "run":
        run()
    elif cmd == "setup":
        setup()
    elif cmd == "push":
        conf = load_conf()
        publish(read_all(), conf)
        print("published")
    elif cmd == "usage":
        print(json.dumps(read_all(), indent=1))
    elif cmd == "url":
        print(creation_url() or "not set up: run `agent-meter setup`")
    elif cmd == "qr":
        write_qr(load_conf())
    elif cmd == "qr-text":
        conf = load_conf()
        if not conf.get("gist"):
            sys.exit("not set up: run `agent-meter setup`")
        print(qr_payload(conf))
    elif cmd == "rotate":
        rotate()
    else:
        print(__doc__.strip())


AGENTS = [Agent("claude", "claude", read_claude, status=claude_server)]

if __name__ == "__main__":
    main()
