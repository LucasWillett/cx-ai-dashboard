#!/usr/bin/env python3
"""
Weekly growth snapshot — appends current total hours to growth.json.
Run via launchd every Friday, or manually: python3 snapshot_growth.py
Also supports --backfill to generate historical data from addedDate fields.
"""

import json
import os
from datetime import datetime, timedelta

DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(DIR, "data.json")
GROWTH_FILE = os.path.join(DIR, "growth.json")


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default if default is not None else []


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def compute_hours_at_date(data, target_date):
    """Compute total weekly hours for projects that existed by target_date."""
    total = 0
    for team in data["teams"]:
        for proj in team["projects"]:
            added = proj.get("addedDate")
            if added and added <= target_date:
                total += proj["weeklyMinutes"]
    return round(total / 60, 1)


def snapshot():
    """Append today's total to growth.json."""
    data = load_json(DATA_FILE)
    growth = load_json(GROWTH_FILE, [])

    total_minutes = sum(
        p["weeklyMinutes"]
        for t in data["teams"]
        for p in t["projects"]
    )
    today = datetime.now().strftime("%Y-%m-%d")
    week_label = datetime.now().strftime("%b %-d")

    # Don't duplicate if already snapped today
    if growth and growth[-1].get("date") == today:
        growth[-1]["hours"] = round(total_minutes / 60, 1)
        growth[-1]["week"] = week_label
    else:
        growth.append({
            "date": today,
            "week": week_label,
            "hours": round(total_minutes / 60, 1)
        })

    save_json(GROWTH_FILE, growth)
    print(f"Snapshot saved: {today} — {round(total_minutes / 60, 1)} hrs/wk")


def backfill():
    """Generate historical weekly snapshots from addedDate fields."""
    data = load_json(DATA_FILE)

    # Find earliest addedDate
    all_dates = [
        p["addedDate"]
        for t in data["teams"]
        for p in t["projects"]
        if p.get("addedDate")
    ]
    if not all_dates:
        print("No addedDate fields found — nothing to backfill.")
        return

    start = datetime.strptime(min(all_dates), "%Y-%m-%d")
    end = datetime.now()

    # Generate weekly snapshots (every Monday)
    growth = []
    current = start - timedelta(days=start.weekday())  # Align to Monday
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        week_label = current.strftime("%b %-d")
        hours = compute_hours_at_date(data, date_str)
        if hours > 0:
            growth.append({
                "date": date_str,
                "week": week_label,
                "hours": hours
            })
        current += timedelta(weeks=1)

    # Add today if not already covered
    today = datetime.now().strftime("%Y-%m-%d")
    if not growth or growth[-1]["date"] != today:
        hours = compute_hours_at_date(data, today)
        growth.append({
            "date": today,
            "week": datetime.now().strftime("%b %-d"),
            "hours": hours
        })

    save_json(GROWTH_FILE, growth)
    print(f"Backfilled {len(growth)} snapshots from {growth[0]['date']} to {growth[-1]['date']}")


if __name__ == "__main__":
    import sys
    if "--backfill" in sys.argv:
        backfill()
    else:
        snapshot()
