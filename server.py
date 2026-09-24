"""Kindle family calendar: a tiny TRMNL-compatible server.

The Kindle runs KOReader + trmnl.koplugin, pointed at this server (Base URL).

GET /api/display         -> {"image_url", "filename", "refresh_rate"}  (polled by the plugin)
GET /screens/<name>.png  -> the rendered screen
GET /preview             -> the HTML that gets screenshotted, for tweaking in a browser
"""
import hashlib
import html
import json
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
TZ = ZoneInfo(CONFIG.get("timezone", "America/Los_Angeles"))
WIDTH, HEIGHT = 758, 1024  # Kindle Paperwhite 1
SCREENS = ROOT / "screens"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
GRAY_PROFILE = "/System/Library/ColorSync/Profiles/Generic Gray Gamma 2.2 Profile.icc"
ICS_TTL = 600  # seconds to cache each calendar feed

_ics_cache = {}  # url -> (fetched_at, Calendar)
_render_lock = threading.Lock()


# --- Calendar data ---------------------------------------------------------

def fetch_calendar(url):
    cached = _ics_cache.get(url)
    if cached and time.time() - cached[0] < ICS_TTL:
        return cached[1]
    with urllib.request.urlopen(url, timeout=20) as resp:
        cal = icalendar.Calendar.from_ical(resp.read())
    _ics_cache[url] = (time.time(), cal)
    return cal


def events_on(day):
    """All events overlapping `day` across configured calendars, sorted."""
    start = datetime.combine(day, datetime.min.time(), TZ)
    end = start + timedelta(days=1)
    events, errors = [], []
    for source in CONFIG["calendars"]:
        try:
            cal = fetch_calendar(source["url"])
        except Exception as e:
            errors.append(f"{source['name']}: {e}")
            continue
        for ev in recurring_ical_events.of(cal).between(start, end):
            dtstart = ev.get("DTSTART").dt
            dtend = ev.get("DTEND").dt if ev.get("DTEND") else dtstart
            all_day = not isinstance(dtstart, datetime)
            if not all_day:
                dtstart, dtend = dtstart.astimezone(TZ), dtend.astimezone(TZ)
            events.append({
                "title": str(ev.get("SUMMARY", "(no title)")),
                "all_day": all_day,
                "start": dtstart,
                "end": dtend,
                "calendar": source["name"],
            })
    events.sort(key=lambda e: (not e["all_day"], e["start"] if not e["all_day"] else datetime.min.replace(tzinfo=TZ)))
    return events, errors


# --- Rendering ---------------------------------------------------------------

def fmt_time(dt):
    return dt.strftime("%-I:%M").replace(":00", "") + dt.strftime("%p").lower()[0]


def event_rows(events, now, multi_cal):
    rows = []
    for e in events:
        past = not e["all_day"] and e["end"] <= now
        when = "all day" if e["all_day"] else fmt_time(e["start"])
        label = f'<span class="cal">{html.escape(e["calendar"])}</span>' if multi_cal else ""
        rows.append(
            f'<li class="{"past" if past else ""}">'
            f'<span class="when">{when}</span>'
            f'<span class="what">{html.escape(e["title"])}{label}</span></li>'
        )
    return "\n".join(rows) or '<li class="empty">Nothing scheduled</li>'


def build_html(now=None):
    now = now or datetime.now(TZ)
    today, tomorrow = now.date(), now.date() + timedelta(days=1)
    today_events, errors = events_on(today)
    tomorrow_events, _ = events_on(tomorrow)
    multi_cal = len(CONFIG["calendars"]) > 1
    error_html = f'<p class="error">Calendar unavailable: {html.escape("; ".join(errors))}</p>' if errors else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden; background: #fff; color: #000;
          font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; padding: 48px 44px; }}
  header {{ border-bottom: 4px solid #000; padding-bottom: 20px; margin-bottom: 28px; }}
  h1 {{ font-family: Georgia, serif; font-size: 76px; line-height: 1; letter-spacing: -1px; }}
  .date {{ font-size: 34px; margin-top: 10px; color: #333; }}
  h2 {{ font-size: 22px; text-transform: uppercase; letter-spacing: 3px; color: #555; margin: 0 0 14px; }}
  ul {{ list-style: none; }}
  li {{ display: flex; align-items: baseline; font-size: 32px; line-height: 1.25; padding: 12px 0;
        border-bottom: 1px solid #bbb; }}
  li.past {{ color: #888; }}
  li.empty {{ color: #777; font-style: italic; border: none; }}
  .when {{ flex: 0 0 150px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .what {{ flex: 1; }}
  .cal {{ font-size: 20px; color: #666; margin-left: 12px; }}
  section.tomorrow {{ margin-top: 40px; }}
  section.tomorrow li {{ font-size: 25px; padding: 8px 0; }}
  section.tomorrow .when {{ flex-basis: 150px; }}
  .error {{ position: absolute; bottom: 24px; left: 44px; right: 44px; font-size: 18px; color: #555; }}
</style></head>
<body>
  <header>
    <h1>{today.strftime("%A")}</h1>
    <div class="date">{today.strftime("%B %-d, %Y")}</div>
  </header>
  <section class="today"><h2>Today</h2><ul>{event_rows(today_events, now, multi_cal)}</ul></section>
  <section class="tomorrow"><h2>Tomorrow · {tomorrow.strftime("%A")}</h2><ul>{event_rows(tomorrow_events, now, multi_cal)}</ul></section>
  {error_html}
</body></html>"""


def render_png(page_html):
    """Screenshot the page with headless Chrome; returns the screen filename (content-addressed)."""
    key = hashlib.sha1(page_html.encode()).hexdigest()[:12]
    name = f"cal-{key}.png"
    out = SCREENS / name
    with _render_lock:
        if not out.exists():
            SCREENS.mkdir(exist_ok=True)
            src = SCREENS / f"{key}.html"
            src.write_text(page_html)
            subprocess.run([
                CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                "--force-device-scale-factor=1", f"--window-size={WIDTH},{HEIGHT}",
                f"--screenshot={out}", src.as_uri(),
            ], check=True, capture_output=True, timeout=60)
            src.unlink()
            # Grayscale shrinks the file and matches the panel; ignore if sips can't
            subprocess.run(["sips", "--matchTo", GRAY_PROFILE, str(out)], capture_output=True)
            for old in sorted(SCREENS.glob("cal-*.png"), key=lambda p: p.stat().st_mtime)[:-10]:
                old.unlink()
    return name


# --- HTTP ----------------------------------------------------------------------

def log_poll(headers):
    with open(ROOT / "polls.csv", "a") as f:
        f.write(f'{datetime.now(TZ).isoformat(timespec="seconds")},{headers.get("percent-charged", "")},{headers.get("ID", "")}\n')


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/display":
            token = CONFIG.get("token")
            if token and self.headers.get("access-token") != token:
                return self.send(401, b'{"error": "bad access-token"}', "application/json")
            log_poll(self.headers)
            name = render_png(build_html())
            body = json.dumps({
                "image_url": f'http://{CONFIG["public_host"]}:{CONFIG["port"]}/screens/{name}',
                "filename": name.removesuffix(".png"),
                "refresh_rate": CONFIG.get("refresh_seconds", 900),
            }).encode()
            return self.send(200, body, "application/json")
        if path.startswith("/screens/") and path.endswith(".png"):
            f = SCREENS / Path(path).name
            if f.exists():
                return self.send(200, f.read_bytes(), "image/png")
        if path == "/preview":
            return self.send(200, build_html().encode(), "text/html; charset=utf-8")
        self.send(404, b"not found", "text/plain")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "render":
        print(SCREENS / render_png(build_html()))
        return
    server = ThreadingHTTPServer(("0.0.0.0", CONFIG["port"]), Handler)
    print(f'Serving on http://{CONFIG["public_host"]}:{CONFIG["port"]}  (preview: /preview)')
    server.serve_forever()


if __name__ == "__main__":
    main()
