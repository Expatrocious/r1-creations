#!/usr/bin/env python3
"""End-to-end checks for the r1 creation in headless Chromium at the r1's 240x282.

GitHub is never contacted: requests to api.github.com and gist.githubusercontent.com
are answered by the test (CDP Fetch interception) with made-up usage data.
Run with tests/run.sh (it starts a static server for ../../agent-meter/ and the browser).
usage: test_creation.py <cdp port> <site url> [screenshot dir]
"""
import asyncio, base64, json, sys, time
from datetime import datetime, timedelta, timezone
import aiohttp

PORT, SITE = int(sys.argv[1]), sys.argv[2]
SHOTS = sys.argv[3] if len(sys.argv) > 3 else None
GIST = "0123456789abcdef0123456789abcdef"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail}" if detail and not ok else ""))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def doc(session=32.0, week=61.0, age_min=1, agents=1, error=None, server="active", extra=False):
    now = datetime.now(timezone.utc)
    a = {"id": "claude", "name": "claude", "plan": "pro", "server": server, "error": error,
         "updated_at": iso(now - timedelta(minutes=age_min)),
         "windows": [
             {"id": "five_hour", "label": "session", "pct": session,
              "resets_at": (now + timedelta(hours=1, minutes=44)).isoformat(), "period_s": 18000},
             {"id": "seven_day", "label": "week", "pct": week,
              "resets_at": iso(now + timedelta(days=1, hours=13)), "period_s": 604800}],
         "extra": {"pct": 12.5} if extra else None}
    out = {"v": 2, "published_at": iso(now), "agents": [a]}
    for i in range(1, agents):
        b = json.loads(json.dumps(a))
        b.update({"id": f"agent{i}", "name": f"agent {i}", "plan": None})
        b["windows"] = b["windows"][:1]
        out["agents"].append(b)
    return out


class CDP:
    def __init__(self, ws):
        self.ws, self.n, self.pending, self.errors = ws, 0, {}, []
        self.mode = {"api": 200, "raw": 200}
        self.data = doc()
        self.hits = {"api": 0, "raw": 0}

    async def reader(self):
        async for msg in self.ws:
            d = json.loads(msg.data)
            if "id" in d and d["id"] in self.pending:
                self.pending.pop(d["id"]).set_result(d)
            elif d.get("method") == "Fetch.requestPaused":
                asyncio.create_task(self.answer(d["params"]))
            elif d.get("method") == "Runtime.exceptionThrown":
                self.errors.append(str(d["params"]["exceptionDetails"].get("exception", {}).get("description", "exception")))
            elif d.get("method") == "Runtime.consoleAPICalled" and d["params"]["type"] == "error":
                self.errors.append(" ".join(str(a.get("value", "")) for a in d["params"]["args"]))

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send_json({"id": self.n, "method": method, "params": params})
        d = await asyncio.wait_for(fut, 20)
        if "error" in d:
            raise RuntimeError(f"{method}: {d['error']}")
        return d.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError("JS: " + str(r["exceptionDetails"]))
        return r.get("result", {}).get("value")

    async def answer(self, p):
        url, rid = p["request"]["url"], p["requestId"]
        cors = [{"name": "Access-Control-Allow-Origin", "value": "*"},
                {"name": "Access-Control-Expose-Headers", "value": "X-RateLimit-Remaining, X-RateLimit-Reset"}]
        if url.startswith("https://api.github.com/gists/"):
            self.hits["api"] += 1
            code = self.mode["api"]
            if code == 200:
                body = json.dumps({"id": GIST, "owner": {"login": "Expatrocious"},
                                   "files": {"usage.json": {"content": json.dumps(self.data), "truncated": False}}})
                hdr = cors + [{"name": "Content-Type", "value": "application/json"},
                              {"name": "X-RateLimit-Remaining", "value": "57"}]
            else:
                body = json.dumps({"message": "API rate limit exceeded"})
                hdr = cors + [{"name": "X-RateLimit-Remaining", "value": "0"},
                              {"name": "X-RateLimit-Reset", "value": str(int(time.time()) + 3600)}]
        elif url.startswith("https://gist.githubusercontent.com/"):
            self.hits["raw"] += 1
            code = self.mode["raw"]
            body = json.dumps(self.data) if code == 200 else "Not Found"
            hdr = cors + [{"name": "Content-Type", "value": "text/plain; charset=utf-8"}]
        else:
            await self.call("Fetch.continueRequest", requestId=rid)
            return
        if code == 0:
            await self.call("Fetch.failRequest", requestId=rid, errorReason="InternetDisconnected")
            return
        await self.call("Fetch.fulfillRequest", requestId=rid, responseCode=code, responseHeaders=hdr,
                        body=base64.b64encode(body.encode()).decode())


R1_STORAGE = """
window.__store = {};
window.creationStorage = { plain: {
  getItem: (k) => Promise.resolve(window.__store[k] || null),
  setItem: (k, v) => { atob(v); window.__store[k] = v; return Promise.resolve(); },
} };
"""


async def main():
    async with aiohttp.ClientSession() as http:
        tabs = await (await http.get(f"http://127.0.0.1:{PORT}/json")).json()
        page = [t for t in tabs if t.get("type") == "page"][0]
        async with http.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            c = CDP(ws)
            asyncio.create_task(c.reader())
            await c.call("Runtime.enable")
            await c.call("Page.enable")
            await c.call("Emulation.setDeviceMetricsOverride", width=240, height=282, deviceScaleFactor=2, mobile=True)
            await c.call("Fetch.enable", patterns=[{"urlPattern": "https://api.github.com/*"},
                                                   {"urlPattern": "https://gist.githubusercontent.com/*"}])
            await c.call("Page.addScriptToEvaluateOnNewDocument", source=R1_STORAGE)
            await c.call("Page.bringToFront")

            async def open_(query=""):
                await c.call("Page.navigate", url=SITE + query)
                await asyncio.sleep(0.3)
                await c.call("Page.bringToFront")      # headless leaves a navigated tab hidden
                await asyncio.sleep(1.3)

            async def shot(name):
                if SHOTS:
                    r = await c.call("Page.captureScreenshot", format="png")
                    open(f"{SHOTS}/{name}.png", "wb").write(base64.b64decode(r["data"]))

            q = f"?g={GIST}&u=Expatrocious"

            # 1. opened without a gist: setup hint, nothing fetched
            await open_()
            t = await c.js("document.getElementById('splash-text').textContent")
            check("no gist: setup hint on the splash", "Scan the agent meter QR" in t, t)
            check("no gist: GitHub not contacted", c.hits["api"] == 0 and c.hits["raw"] == 0, str(c.hits))

            # 2. normal reading through the API
            await open_(q)
            await asyncio.sleep(1.2)
            info = await c.js("""(() => ({
              loading: document.getElementById('app').classList.contains('loading'),
              nums: [...document.querySelectorAll('.gauge .num')].map(e => e.textContent),
              labels: [...document.querySelectorAll('.gauge .label')].map(e => e.textContent),
              resets: [...document.querySelectorAll('.gauge .reset')].map(e => e.textContent),
              plan: document.getElementById('plan').textContent,
              status: document.getElementById('status').textContent,
              dot: document.getElementById('dot').className,
              server: document.getElementById('server').textContent,
              insight: document.getElementById('insight').hidden ? '' : document.getElementById('insight').textContent,
              arcs: [...document.querySelectorAll('.arc')].map(e => +e.style.strokeDashoffset),
              pace: document.querySelectorAll('.pace').length,
            }))()""")
            check("reading shown (splash gone)", not info["loading"], str(info))
            check("two gauges with 32% and 61%", info["nums"] == ["32", "61"], str(info["nums"]))
            check("labels session / week", info["labels"] == ["session", "week"], str(info["labels"]))
            check("countdowns", info["resets"][0] in ("resets in 1h 44m", "resets in 1h 43m") and info["resets"][1].startswith("resets in 1d 1"), str(info["resets"]))
            check("plan pill", info["plan"].lower() == "pro", info["plan"])
            check("footer live", info["status"].startswith("live") and "live" in info["dot"], info["status"] + " / " + info["dot"])
            check("phone sessions shown", info["server"] == "sessions on", info["server"])
            C = 2 * 3.141592653589793 * 37
            check("rings drawn to the percentages", abs(info["arcs"][0] - C * 0.68) < 1 and abs(info["arcs"][1] - C * 0.39) < 1, str(info["arcs"]))
            check("pace markers on both rings", info["pace"] == 2, str(info["pace"]))
            check("pace insight for the week", "pace this week" in info["insight"], info["insight"])
            fits = await c.js("""(() => { const out = [];
              for (const e of document.querySelectorAll('#app *')) { if (e.closest('#splash')) continue;
                const r = e.getBoundingClientRect(); if (!r.width && !r.height) continue;
                if (r.left < -0.5 || r.top < -0.5 || r.right > 240.5 || r.bottom > 282.5) out.push(e.tagName + '.' + e.className + ' ' + JSON.stringify([r.left, r.top, r.right, r.bottom])); }
              return out; })()""")
            check("everything fits in 240x282", not fits, "; ".join(fits[:4]))
            overlap = await c.js("""(() => { const f = document.getElementById('footer').getBoundingClientRect();
              const last = [...document.querySelectorAll('#gauges, #insight:not([hidden]), #more')].map(e => e.getBoundingClientRect().bottom);
              return Math.max(...last) <= f.top + 0.5; })()""")
            check("content stays above the footer", overlap)
            stored = await c.js("JSON.parse(decodeURIComponent(escape(atob(window.__store['agent-meter'] || ''))) || 'null')")
            check("reading saved in r1 storage (base64)", stored and stored.get("gist") == GIST and stored.get("data"), str(stored)[:120])
            await shot("01-normal")

            # 3. side button checks GitHub again (and is rate limited to one per 8 s)
            await asyncio.sleep(8.2)
            before = c.hits["api"]
            c.data = doc(session=47.0, week=63.0)
            await c.js("window.dispatchEvent(new CustomEvent('sideClick'))")
            await asyncio.sleep(1.6)
            nums = await c.js("[...document.querySelectorAll('.gauge .num')].map(e => e.textContent)")
            check("side button fetches again", c.hits["api"] == before + 1, str(c.hits))
            check("new numbers after the side button", nums == ["47", "63"], str(nums))
            await c.js("window.dispatchEvent(new CustomEvent('sideClick'))")
            await asyncio.sleep(0.4)
            check("second press within 8 s does not refetch", c.hits["api"] == before + 1, str(c.hits))

            # 4. scroll wheel with one agent flips countdown <-> clock time (12-hour)
            await c.js("window.dispatchEvent(new CustomEvent('scrollDown'))")
            r = await c.js("document.querySelector('.gauge .reset').textContent")
            check("wheel shows reset clock time, 12-hour", ("AM" in r or "PM" in r) and "in " not in r, r)
            await c.js("window.dispatchEvent(new CustomEvent('scrollUp'))")
            r = await c.js("document.querySelector('.gauge .reset').textContent")
            check("wheel back to the countdown", r.startswith("resets in"), r)
            await c.js("document.querySelector('.gauge').click()")
            r = await c.js("document.querySelector('.gauge .reset').textContent")
            check("tapping a ring flips it too", "AM" in r or "PM" in r, r)
            await c.js("document.querySelector('.gauge').click()")

            # 5. API rate limited: falls back to the raw gist file
            c.mode["api"] = 403
            c.data = doc(session=50.0, week=64.0)
            raw0 = c.hits["raw"]
            await asyncio.sleep(8.2)
            await c.js("window.dispatchEvent(new CustomEvent('sideClick'))")
            await asyncio.sleep(1.6)
            nums = await c.js("[...document.querySelectorAll('.gauge .num')].map(e => e.textContent)")
            check("rate limited API -> raw gist used", c.hits["raw"] == raw0 + 1 and nums == ["50", "64"], f"{c.hits} {nums}")
            api0 = c.hits["api"]
            await asyncio.sleep(8.2)
            await c.js("window.dispatchEvent(new CustomEvent('sideClick'))")
            await asyncio.sleep(1.2)
            check("API left alone until its limit resets", c.hits["api"] == api0 and c.hits["raw"] == raw0 + 2, str(c.hits))

            # 6. offline: keeps the last reading, says so
            c.mode["raw"] = 0
            await asyncio.sleep(8.2)
            await c.js("window.dispatchEvent(new CustomEvent('sideClick'))")
            await asyncio.sleep(1.6)
            st = await c.js("[document.getElementById('status').textContent, document.getElementById('dot').className, document.querySelectorAll('.gauge').length]")
            check("no connection keeps the last reading", st[0].startswith("no connection") and "bad" in st[1] and st[2] == 2, str(st))
            await shot("02-offline")

            # 7. a fresh page with nothing cached and no connection: explains on the splash
            await c.js("window.__store = {}; localStorage.clear(); true")
            await c.call("Page.addScriptToEvaluateOnNewDocument", source="window.__store = {};")
            c.mode = {"api": 0, "raw": 0}
            await open_(q)
            await asyncio.sleep(1.0)
            t = await c.js("[document.getElementById('app').classList.contains('loading'), document.getElementById('splash-text').textContent]")
            check("nothing cached + offline: splash explains", t[0] and t[1].startswith("Can’t reach GitHub right now. Press the side button"), str(t))
            await shot("03-splash-offline")

            # 8. the PC stopped publishing an hour ago: PC offline, dimmed
            c.mode = {"api": 200, "raw": 200}
            c.data = doc(age_min=70)
            await open_(q)
            await asyncio.sleep(1.2)
            st = await c.js("[document.getElementById('status').textContent, document.getElementById('app').classList.contains('stale')]")
            check("old reading -> 'PC offline', dimmed", st[0].startswith("PC offline") and st[1], str(st))

            # 9. nearly used up: red ring and a warning line
            c.data = doc(session=93.0, week=70.0, extra=True)
            await open_(q)
            await asyncio.sleep(1.4)
            st = await c.js("""[document.querySelector('.gauge').classList.contains('hot'),
              getComputedStyle(document.querySelector('.arc')).stroke, document.getElementById('insight').textContent,
              [...document.querySelectorAll('.mini .name')].map(e => e.textContent)]""")
            check("93% session ring turns red", st[0] and st[1] in ("rgb(255, 59, 48)",), str(st[:2]))
            check("warning line names the session", st[2].startswith("session nearly out · resets 1h 4"), st[2])
            clipped = await c.js("(() => { const e = document.getElementById('insight'); return e.scrollWidth > e.clientWidth + 1; })()")
            check("warning line is not cut off", not clipped)
            check("extra usage row shown", st[3] == ["extra usage"], str(st[3]))
            await shot("04-hot")

            # 10. the PC could not read usage: says so, keeps numbers
            c.data = doc(error="claude gave no usage")
            await open_(q)
            await asyncio.sleep(1.2)
            st = await c.js("document.getElementById('status').textContent")
            check("PC read error shown in the footer", st.startswith("PC can’t read usage"), st)

            # 11. two agents: pager dots, wheel switches agents
            c.data = doc(agents=2)
            await open_(q)
            await asyncio.sleep(1.2)
            st = await c.js("[document.querySelectorAll('#pager i').length, document.getElementById('agent-name').textContent]")
            await c.js("window.dispatchEvent(new CustomEvent('scrollDown'))")
            st2 = await c.js("[document.getElementById('agent-name').textContent, document.getElementById('gauges').className, document.getElementById('plan').hidden]")
            check("two agents: two pager dots", st[0] == 2 and st[1] == "claude", str(st))
            check("wheel moves to the next agent", st2[0] == "agent 1" and st2[1] == "single" and st2[2], str(st2))
            await shot("05-second-agent")

            # 12. the gist was deleted (QR rotated): tells you to scan the new one
            await c.js("window.__store = {}; true")
            c.mode = {"api": 404, "raw": 404}
            await open_(q)
            await asyncio.sleep(1.2)
            t = await c.js("document.getElementById('splash-text').textContent")
            check("old QR: says to scan the new one", "replaced" in t, t)

            check("no JS errors", not c.errors, "; ".join(c.errors[:3]))
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


asyncio.run(main())
