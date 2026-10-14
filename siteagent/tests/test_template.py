"""End-to-end test of the booking/admin template.

Usage:  python tests/test_template.py <path-to-built-template-dir>
(the dir must already have run `npm install && npm run build`)
"""
import datetime as dt
import os
import signal
import subprocess
import sys
import tempfile
import time

import httpx

root = sys.argv[1]
port = 4399
base = f"http://127.0.0.1:{port}"
tmp = tempfile.mkdtemp()
env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(port), "ADMIN_PASSWORD": "pw-test",
       "SESSION_SECRET": "s" * 32, "DB_PATH": f"{tmp}/t.db"}
srv = subprocess.Popen(["node", "--disable-warning=ExperimentalWarning", "dist/server/entry.mjs"],
                       cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
fails = 0


def check(name, cond, extra=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if (extra and not cond) else ""))
    fails += 0 if cond else 1


try:
    c = httpx.Client(base_url=base, timeout=10)
    for _ in range(50):
        try:
            c.get("/api/services"); break
        except httpx.TransportError:
            time.sleep(0.2)

    svcs = c.get("/api/services").json()["services"]
    check("seeded one service", len(svcs) == 1 and svcs[0]["durationMin"] == 30, svcs)
    sid = svcs[0]["id"]

    today = dt.date.today()
    def next_wd(wd):  # python: Mon=0
        d = today + dt.timedelta(days=2)
        while d.weekday() != wd: d += dt.timedelta(days=1)
        return d.isoformat()
    mon, sun = next_wd(0), next_wd(6)

    slots = c.get("/api/availability", params={"date": mon, "service": sid}).json()["slots"]
    check("monday has slots 09:00..16:30", slots[0] == "09:00" and slots[-1] == "16:30" and len(slots) == 16, slots[:3])
    check("sunday closed", c.get("/api/availability", params={"date": sun, "service": sid}).json()["slots"] == [])
    check("past date has no slots", c.get("/api/availability", params={"date": "2020-01-06", "service": sid}).json()["slots"] == [])
    check("bad date 400", c.get("/api/availability", params={"date": "nope", "service": sid}).status_code == 400)

    body = {"serviceId": sid, "date": mon, "time": "10:00", "name": "Ann", "email": "ann@example.com"}
    r = c.post("/api/book", json=body)
    check("book ok 201", r.status_code == 201, r.text)
    r2 = c.post("/api/book", json=body)
    check("double book 409", r2.status_code == 409, r2.text)
    check("slot removed", "10:00" not in c.get("/api/availability", params={"date": mon, "service": sid}).json()["slots"])
    check("bad email 400", c.post("/api/book", json={**body, "time": "11:00", "email": "x"}).status_code == 400)
    check("non-json 415", c.post("/api/book", content="a=b", headers={"content-type": "text/plain"}).status_code == 415)
    check("honeypot silently ok, no booking",
          c.post("/api/book", json={**body, "time": "12:00", "website": "spam"}).status_code == 200
          and "12:00" in c.get("/api/availability", params={"date": mon, "service": sid}).json()["slots"])

    # ---- admin gate ----
    for path in ["/api/admin/bookings", "/api/admin/days-off", "/api/admin/hours", "/api/admin/services", "/api/admin/settings"]:
        check(f"401 without login {path}", c.get(path).status_code == 401)
    check("wrong password 401", c.post("/api/admin/login", json={"password": "bad"}).status_code == 401)
    r = c.post("/api/admin/login", json={"password": "pw-test"})
    check("login ok", r.status_code == 200, r.text)
    cookie = r.headers["set-cookie"].split(";")[0]
    check("cookie is HttpOnly+Strict", "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower())
    a = httpx.Client(base_url=base, timeout=10, headers={"cookie": cookie})
    tampered = httpx.Client(base_url=base, headers={"cookie": cookie[:-3] + "abc"})
    check("tampered cookie 401", tampered.get("/api/admin/bookings").status_code == 401)

    # ---- days off ----
    r = a.post("/api/admin/days-off", json={"from": mon, "reason": "vacation"})
    check("close monday", r.status_code == 200 and r.json()["closed"] == 1 and r.json()["affectedBookings"] == 1, r.text)
    check("closed day has no slots", c.get("/api/availability", params={"date": mon, "service": sid}).json()["slots"] == [])
    month = mon[:7]
    cal = c.get("/api/calendar", params={"month": month, "service": sid}).json()["days"]
    check("calendar marks monday closed", cal[mon] is False)
    check("book on closed day 409", c.post("/api/book", json={**body, "time": "13:00"}).status_code == 409)
    check("bad range 400", a.post("/api/admin/days-off", json={"from": mon, "to": "2000-01-01"}).status_code == 400)
    r = a.post("/api/admin/days-off", json={"from": mon, "to": (dt.date.fromisoformat(mon) + dt.timedelta(days=3)).isoformat()})
    check("range close = 4 days", r.json()["closed"] == 4, r.text)
    check("list days off", len(a.get("/api/admin/days-off").json()["daysOff"]) == 4)
    a.delete("/api/admin/days-off", params={"date": mon})
    check("reopen day", len(c.get("/api/availability", params={"date": mon, "service": sid}).json()["slots"]) > 0)

    # ---- weekly hours ----
    hrs = a.get("/api/admin/hours").json()["hours"]
    for h in hrs:
        if h["weekday"] == 0: h.update(closed=0, open="10:00", close="12:00")
    check("save hours", a.put("/api/admin/hours", json={"hours": hrs}).status_code == 200)
    check("sunday now open 10-12", c.get("/api/availability", params={"date": sun, "service": sid}).json()["slots"] == ["10:00", "10:30", "11:00", "11:30"])
    hrs[0].update(close="09:00")
    check("close<open rejected", a.put("/api/admin/hours", json={"hours": hrs}).status_code == 400)

    # ---- services / overlap / capacity ----
    r = a.post("/api/admin/services", json={"name": "Deep session", "durationMin": 90, "price": 120})
    sid2 = r.json()["id"]
    check("create service", r.status_code == 201)
    slots2 = c.get("/api/availability", params={"date": sun, "service": sid2}).json()["slots"]
    check("90min fits 10:00-12:00 only at 10:00, 10:30", slots2 == ["10:00", "10:30"], slots2)
    r = c.post("/api/book", json={**body, "serviceId": sid2, "date": sun, "time": "10:00"})
    check("book 90min", r.status_code == 201, r.text)
    check("overlap blocks 30min slots until 11:30", c.get("/api/availability", params={"date": sun, "service": sid}).json()["slots"] == ["11:30"])
    a.put("/api/admin/settings", json={"capacity": 2})
    check("capacity 2 reopens slots", len(c.get("/api/availability", params={"date": sun, "service": sid}).json()["slots"]) == 4)
    check("settings validation", a.put("/api/admin/settings", json={"capacity": 0}).status_code == 400)
    a.post("/api/admin/services", json={"id": sid2, "name": "Deep session", "durationMin": 90, "price": 120, "active": False})
    check("inactive service hidden", all(s["id"] != sid2 for s in c.get("/api/services").json()["services"]))

    # ---- bookings admin ----
    bks = a.get("/api/admin/bookings").json()["bookings"]
    check("admin lists bookings", len(bks) == 2, bks)
    bid = next(b["id"] for b in bks if b["service"] == "Consultation")
    check("cancel booking", a.patch("/api/admin/bookings", json={"id": bid, "status": "cancelled"}).status_code == 200)
    check("cancelled slot reopens", "10:00" in c.get("/api/availability", params={"date": mon, "service": sid}).json()["slots"])

    # ---- pages ----
    for p in ["/", "/book", "/admin"]:
        check(f"page {p} 200", c.get(p).status_code == 200)
    check("admin page noindex", "noindex" in c.get("/admin").text)
finally:
    srv.send_signal(signal.SIGTERM)
    srv.wait(timeout=5)

print(f"\n{'ALL PASSED' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
