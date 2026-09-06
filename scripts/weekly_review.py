"""Weekly review: read every session, aggregate, one stateless Claude call,
then replace a callout block on a fixed Notion page."""
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DB_ID = os.environ["NOTION_DB_ID"]
PAGE_ID = os.environ.get("NOTION_REVIEW_PAGE_ID", "")
BLOCK_ID = os.environ.get("NOTION_REVIEW_BLOCK_ID", "")  # your existing callout block; if set, the review goes inside it
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
TZ = ZoneInfo(os.environ.get("REVIEW_TZ", "Europe/Zurich"))
MIN_SESSIONS = int(os.environ.get("MIN_SESSIONS", "20"))

MARKER = "Weekly review"
API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
TOD_ORDER = ["Morning", "Afternoon", "Evening", "Night"]
DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------- Notion helpers ----------

def notion(method, path, **kw):
    r = None
    for attempt in range(5):
        r = requests.request(method, API + path, headers=HEADERS, timeout=60, **kw)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        return r
    return r


def die(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)


def prop_text(p):
    if not p:
        return ""
    kind = p.get("type")
    if kind in ("rich_text", "title"):
        return "".join(t.get("plain_text", "") for t in p.get(kind, []))
    if kind == "select":
        return (p.get("select") or {}).get("name", "") or ""
    return ""


# ---------- data ----------

def fetch_sessions():
    rows, cursor = [], None
    while True:
        body = {"page_size": 100, "sorts": [{"property": "Start", "direction": "ascending"}]}
        if cursor:
            body["start_cursor"] = cursor
        r = notion("POST", f"/databases/{DB_ID}/query", json=body)
        if r.status_code != 200:
            die(f"query failed ({r.status_code}): {r.text[:300]}")
        data = r.json()
        for page in data.get("results", []):
            p = page.get("properties", {})
            start_raw = ((p.get("Start") or {}).get("date") or {}).get("start")
            minutes = (p.get("Minutes") or {}).get("number")
            if not start_raw or not minutes:
                continue
            try:
                dt = datetime.fromisoformat(start_raw)
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            local = dt.astimezone(TZ)
            rows.append({
                "start": local,
                "minutes": int(minutes),
                "completed": bool((p.get("Completed") or {}).get("checkbox", False)),
                "focus": prop_text(p.get("Focus")) or None,
                "energy": (p.get("Energy") or {}).get("number"),
                "starting": prop_text(p.get("Starting")) or None,
                "environment": prop_text(p.get("Environment")) or None,
                "note": prop_text(p.get("Note"))[:200] or None,
            })
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return rows


def tod(dt):
    h = dt.hour
    return "Night" if h < 6 else "Morning" if h < 12 else "Afternoon" if h < 18 else "Evening"


def week_start(d):
    return d - timedelta(days=d.weekday())


def avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def pct_good(rows):
    answered = [r for r in rows if r["focus"]]
    if not answered:
        return None
    return round(100 * sum(1 for r in answered if r["focus"] == "Good") / len(answered))


def summarize(rows):
    if not rows:
        return {"sessions": 0, "minutes": 0}
    def counts(key):
        c = defaultdict(int)
        for r in rows:
            if r[key]:
                c[r[key]] += 1
        return dict(c)
    return {
        "sessions": len(rows),
        "minutes": sum(r["minutes"] for r in rows),
        "avg_minutes": round(sum(r["minutes"] for r in rows) / len(rows)),
        "completed_pct": round(100 * sum(1 for r in rows if r["completed"]) / len(rows)),
        "avg_energy": avg([r["energy"] for r in rows]),
        "good_focus_pct": pct_good(rows),
        "focus": counts("focus"),
        "starting": counts("starting"),
        "environment": counts("environment"),
    }


def by_bucket(rows, keyfn, order):
    groups = defaultdict(list)
    for r in rows:
        groups[keyfn(r)].append(r)
    return [
        {"bucket": b, "sessions": len(g), "minutes": sum(r["minutes"] for r in g),
         "avg_energy": avg([r["energy"] for r in g]), "good_focus_pct": pct_good(g)}
        for b in order if (g := groups.get(b))
    ]


def build_data(rows, today):
    this_ws = week_start(today)
    last_ws = this_ws - timedelta(days=7)
    this_week = [r for r in rows if r["start"].date() >= this_ws]
    last_week = [r for r in rows if last_ws <= r["start"].date() < this_ws]

    weeks = defaultdict(list)
    for r in rows:
        weeks[week_start(r["start"].date())].append(r)
    history = [
        {"week_of": ws.isoformat(), "sessions": len(g), "minutes": sum(r["minutes"] for r in g),
         "avg_energy": avg([r["energy"] for r in g]), "good_focus_pct": pct_good(g)}
        for ws, g in sorted(weeks.items())[-8:]
    ]

    return {
        "week_of": this_ws.isoformat(),
        "week_end": (this_ws + timedelta(days=6)).isoformat(),
        "all_time_sessions": len(rows),
        "this_week": summarize(this_week),
        "last_week": summarize(last_week),
        "this_week_by_time_of_day": by_bucket(this_week, lambda r: tod(r["start"]), TOD_ORDER),
        "this_week_by_weekday": by_bucket(this_week, lambda r: DAY_ORDER[r["start"].weekday()], DAY_ORDER),
        "all_time_by_time_of_day": by_bucket(rows, lambda r: tod(r["start"]), TOD_ORDER),
        "all_time_by_weekday": by_bucket(rows, lambda r: DAY_ORDER[r["start"].weekday()], DAY_ORDER),
        "weekly_history": history,
        "this_week_sessions": [
            {"when": r["start"].strftime("%a %H:%M"), "min": r["minutes"], "completed": r["completed"],
             "focus": r["focus"], "energy": r["energy"], "starting": r["starting"],
             "env": r["environment"], "note": r["note"]}
            for r in this_week
        ],
    }, this_week


# ---------- Claude ----------

SYSTEM = """You write a short weekly review of one person's study sessions. The person is a biology student
who logs each focus session with: minutes, whether it ran to completion, focus quality, energy (1 = drained,
5 = fresh), how hard it was to start, and environment. They want to learn WHEN they work best and how their
energy behaves, so they can shape their schedule. They already know which subjects they like; do not discuss subjects.

Rules:
- Calm, plain, non-judgmental. No cheerleading, no guilt, no exclamation marks, no streak talk.
- Say what the data supports and nothing more. With few sessions, say patterns are tentative.
- Separate correlation from cause. Low evening energy may mean they only study late when already tired.
- Prefer aggregates over single rows. Mention a single session only if it is clearly informative.
- Compare to previous weeks when history exists.
- At most two experiments, each concrete and testable in one week.
- Never invent numbers. Only use numbers present in the data.

Respond with ONLY a JSON object, no markdown fences, no preamble:
{"summary": "2-3 sentences", "strength": "one sentence", "weakness": "one sentence",
 "experiments": ["one sentence", "one sentence"]}"""


def ask_claude(data):
    if not ANTHROPIC_API_KEY:
        die("ANTHROPIC_API_KEY missing")
    body = {
        "model": MODEL,
        "max_tokens": 800,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": "Data:\n" + json.dumps(data, ensure_ascii=False)}],
    }
    r = None
    for attempt in range(4):
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, json=body, headers={
            "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json",
        })
        if r.status_code in (429, 500, 529):
            time.sleep(3 * (attempt + 1))
            continue
        break
    if r is None or r.status_code != 200:
        die(f"claude failed ({getattr(r, 'status_code', '?')}): {getattr(r, 'text', '')[:300]}")
    text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        out = {"summary": text[:1500], "strength": "", "weakness": "", "experiments": []}
    out["experiments"] = [e for e in (out.get("experiments") or []) if e][:2]
    return out


# ---------- blocks ----------

def rt(text, bold=False, italic=False):
    return {"type": "text", "text": {"content": str(text)[:1900]},
            "annotations": {"bold": bold, "italic": italic}}


def paragraph(*parts):
    return {"type": "paragraph", "paragraph": {"rich_text": list(parts)}}


def bullet(text):
    return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [rt(text)]}}


def fmtnum(v, suffix=""):
    return "–" if v is None else f"{v}{suffix}"


def table_block(rows_by_tod):
    cells = lambda *vals: [[rt(v)] for v in vals]
    rows = [{"type": "table_row", "table_row": {"cells": cells("Time of day", "Sessions", "Minutes", "Energy", "Good focus")}}]
    for b in rows_by_tod:
        rows.append({"type": "table_row", "table_row": {"cells": cells(
            b["bucket"], b["sessions"], b["minutes"], fmtnum(b["avg_energy"]), fmtnum(b["good_focus_pct"], "%"))}})
    return {"type": "table", "table": {"table_width": 5, "has_column_header": True, "has_row_header": False, "children": rows}}


def table_as_text(rows_by_tod):
    lines = ["Time of day / sessions / minutes / energy / good focus"]
    for b in rows_by_tod:
        lines.append(f"{b['bucket']}: {b['sessions']} / {b['minutes']} / {fmtnum(b['avg_energy'])} / {fmtnum(b['good_focus_pct'], '%')}")
    return paragraph(rt("\n".join(lines)))


# ============================================================
# LAYOUT: everything below decides what the review looks like in Notion.
# Order, wording and which blocks appear are all set in build_review_children().
# The TEXT Claude writes (summary/strength/weakness/experiments) is shaped by
# the SYSTEM prompt further up.
# ============================================================

def build_review_children(data, review, now):
    tw, lw = data["this_week"], data["last_week"]
    ws = datetime.fromisoformat(data["week_of"]); we = datetime.fromisoformat(data["week_end"])
    tod_rows = data["this_week_by_time_of_day"]

    if tw["sessions"]:
        delta = ""
        if lw["sessions"]:
            d = tw["minutes"] - lw["minutes"]
            delta = f", {'+' if d >= 0 else ''}{d} min vs last week"
        numbers = (f"{tw['sessions']} sessions, {tw['minutes']} min, "
                   f"{tw['completed_pct']}% ran to the end, average energy {fmtnum(tw['avg_energy'])}{delta}.")
    else:
        numbers = "No sessions logged this week."

    children = [
        paragraph(rt(f"{MARKER}: {ws:%b} {ws.day} – {we:%b} {we.day}", bold=True),
                  rt(f"  ·  generated {now:%a %d %b, %H:%M}", italic=True)),
        paragraph(rt(numbers, italic=True)),
    ]

    if review is None:
        children.append(paragraph(rt(
            f"{data['all_time_sessions']} of {MIN_SESSIONS} sessions collected so far. "
            "The written review starts once there is enough data to see real patterns rather than noise.")))
        if tod_rows:
            children.append(table_block(tod_rows))
        return children

    children.append(paragraph(rt(review.get("summary", ""))))
    if tod_rows:
        children.append(table_block(tod_rows))
    if review.get("strength"):
        children.append(paragraph(rt("Working well: ", bold=True), rt(review["strength"])))
    if review.get("weakness"):
        children.append(paragraph(rt("Worth watching: ", bold=True), rt(review["weakness"])))
    if review.get("experiments"):
        children.append(paragraph(rt("Try next week", bold=True)))
        children.extend(bullet(e) for e in review["experiments"])
    return children


def wrap_in_callout(children):
    return {
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": "🌿"},
            "color": "green_background",
            "rich_text": [rt(MARKER, bold=True)],
            "children": children,
        },
    }


def list_children(block_id):
    out, cursor = [], None
    while True:
        url = f"/blocks/{block_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        r = notion("GET", url)
        if r.status_code == 404:
            die(f"block/page {block_id} not found: share the page with the integration and check the ID")
        if r.status_code != 200:
            die(f"list children failed ({r.status_code}): {r.text[:300]}")
        data = r.json()
        out.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return out


def archive(block_id):
    r = notion("PATCH", f"/blocks/{block_id}", json={"archived": True})
    return r.status_code == 200


def append_children(parent_id, children, tod_rows):
    r = notion("PATCH", f"/blocks/{parent_id}/children", json={"children": children})
    if r.status_code == 200:
        return
    if r.status_code == 400 and tod_rows:
        # Fall back to a plain-text table if a nested table block is rejected.
        children = [c if c.get("type") != "table" else table_as_text(tod_rows) for c in children]
        r = notion("PATCH", f"/blocks/{parent_id}/children", json={"children": children})
        if r.status_code == 200:
            print("note: table written as text")
            return
    die(f"append failed ({r.status_code}): {r.text[:500]}")


def write_into_existing_callout(children, tod_rows):
    """Keeps your callout, replaces everything inside it."""
    r = notion("GET", f"/blocks/{BLOCK_ID}")
    if r.status_code != 200:
        die(f"callout block not found ({r.status_code}). Copy link to block → ID after '#'. {r.text[:200]}")
    if r.json().get("type") != "callout":
        die(f"block {BLOCK_ID} is a {r.json().get('type')}, not a callout")
    old = list_children(BLOCK_ID)
    removed = sum(1 for b in old if archive(b["id"]))
    print(f"cleared {removed} block(s) inside the callout")
    append_children(BLOCK_ID, children, tod_rows)


def write_new_callout_on_page(children, tod_rows):
    """No block ID given: manage our own callout on the review page."""
    removed = 0
    for b in list_children(PAGE_ID):
        if b.get("type") != "callout":
            continue
        text = "".join(t.get("plain_text", "") for t in b["callout"].get("rich_text", []))
        if text.startswith(MARKER) and archive(b["id"]):
            removed += 1
    print(f"removed {removed} old review callout(s)")
    block = wrap_in_callout(children)
    r = notion("PATCH", f"/blocks/{PAGE_ID}/children", json={"children": [block]})
    if r.status_code == 200:
        return
    if r.status_code == 400 and tod_rows:
        block["callout"]["children"] = [c if c.get("type") != "table" else table_as_text(tod_rows) for c in children]
        r = notion("PATCH", f"/blocks/{PAGE_ID}/children", json={"children": [block]})
        if r.status_code == 200:
            print("note: table written as text")
            return
    die(f"append failed ({r.status_code}): {r.text[:500]}")


# ---------- main ----------

def main():
    now = datetime.now(TZ)
    rows = fetch_sessions()
    print(f"{len(rows)} sessions total")
    data, this_week = build_data(rows, now.date())

    review = None
    if len(rows) >= MIN_SESSIONS and this_week:
        review = ask_claude(data)
        print("review:", json.dumps(review, ensure_ascii=False)[:400])
    elif len(rows) >= MIN_SESSIONS:
        review = {"summary": "No sessions this week, so there is nothing new to read into.",
                  "strength": "", "weakness": "", "experiments": []}

    children = build_review_children(data, review, now)
    tod_rows = data["this_week_by_time_of_day"]
    if BLOCK_ID:
        write_into_existing_callout(children, tod_rows)
    elif PAGE_ID:
        write_new_callout_on_page(children, tod_rows)
    else:
        die("set NOTION_REVIEW_BLOCK_ID (existing callout) or NOTION_REVIEW_PAGE_ID (page)")
    print("done")


if __name__ == "__main__":
    main()
