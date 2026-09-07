"""Write one focus session (from repository_dispatch client_payload) into Notion."""
import json
import os
import re
import sys
import time
from datetime import datetime

import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DB_ID = os.environ["NOTION_DB_ID"]
API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

SELECTS = {
    "focus": ("Focus", {"good": "Good", "mixed": "Mixed", "distracted": "Distracted"}),
    "starting": ("Starting", {"easy": "Easy", "okay": "Okay", "hard": "Hard"}),
    "environment": ("Environment", {"quiet": "Quiet", "okay": "Okay", "noisy": "Noisy"}),
}


def notion(method, path, **kw):
    r = None
    for attempt in range(5):
        r = requests.request(method, API + path, headers=HEADERS, timeout=30, **kw)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        return r
    return r


def fail(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)


def main():
    payload = json.loads(os.environ.get("PAYLOAD") or "{}")
    sid = str(payload.get("id") or "").strip()
    start = str(payload.get("start") or "").strip()
    minutes = payload.get("minutes")

    if not sid or not start or not isinstance(minutes, (int, float)) or minutes <= 0:
        fail(f"invalid payload: {payload}")

    minutes = int(round(minutes))
    completed = bool(payload.get("completed", False))

    # Dedup: a retried dispatch must not create a second row.
    q = notion("POST", f"/databases/{DB_ID}/query", json={
        "filter": {"property": "Session ID", "rich_text": {"equals": sid}},
        "page_size": 1,
    })
    if q.status_code == 200:
        if q.json().get("results"):
            print("already logged, skipping")
            return
    else:
        print(f"warning: dedup query failed ({q.status_code}): {q.text[:200]}")

    weekday = None
    try:
        dt = datetime.fromisoformat(start)
        name = f"{dt:%b} {dt.day}, {dt:%H:%M} – {minutes} min"
        weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()]
    except ValueError:
        name = f"Session – {minutes} min"

    props = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Session ID": {"rich_text": [{"text": {"content": sid}}]},
        "Start": {"date": {"start": start}},
        "Minutes": {"number": minutes},
        "Completed": {"checkbox": completed},
    }

    if weekday:
        props["Weekday"] = {"select": {"name": weekday}}

    # Place: a name only. Coordinates never leave the phone.
    place = re.sub(r"\s+", " ", str(payload.get("place") or "")).strip()[:40]
    if place:
        props["Place"] = {"select": {"name": place}}

    for key, (prop, mapping) in SELECTS.items():
        val = payload.get(key)
        if val in mapping:
            props[prop] = {"select": {"name": mapping[val]}}

    energy = payload.get("energy")
    if isinstance(energy, (int, float)) and 1 <= energy <= 5:
        props["Energy"] = {"number": int(energy)}

    note = str(payload.get("note") or "").strip()
    if note:
        props["Note"] = {"rich_text": [{"text": {"content": note[:1900]}}]}

    r = notion("POST", "/pages", json={"parent": {"database_id": DB_ID}, "properties": props})
    if r.status_code != 200:
        fail(f"notion create failed ({r.status_code}): {r.text[:500]}")

    print("logged")


if __name__ == "__main__":
    main()
