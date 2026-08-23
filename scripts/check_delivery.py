#!/usr/bin/env python3
"""Cloud fallback: dispatch a missing digest without racing an active run."""

import argparse
import json
import os
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")
ROOT = Path(__file__).resolve().parent.parent


def edition_values(now, requested):
    slug = requested if requested in ("morning", "evening") else ("morning" if now.hour < 18 else "evening")
    hour = 8 if slug == "morning" else 18
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return slug, target


def decide(marker_status, runs, target):
    if marker_status == "sent" or marker_status is True:
        return "sent"
    for run in runs:
        try:
            created = datetime.fromisoformat(run.get("createdAt", "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if created >= target.astimezone(UTC) and run.get("status") in ("queued", "in_progress", "waiting", "pending"):
            return "running"
    return "dispatch"


def remote_marker_status(repo, marker_name):
    url = f"https://raw.githubusercontent.com/{repo}/main/data/sent_markers/{marker_name}.json"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "TrendingAI-Fallback/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8")).get("status", "")
    except Exception:
        return ""


def editorial_issue_path(marker_name):
    return ROOT / "data" / "editorial" / f"{marker_name}.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--edition", choices=("current", "morning", "evening"), default="current")
    parser.add_argument("--now", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--runs-json", default="")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now).astimezone(BEIJING) if args.now else datetime.now(BEIJING)
    slug, target = edition_values(now, args.edition)
    marker_name = f"{now:%Y-%m-%d}-{slug}"
    marker_file = ROOT / "data" / "sent_markers" / f"{marker_name}.json"
    marker_status = ""
    if marker_file.exists():
        try:
            marker_status = json.loads(marker_file.read_text(encoding="utf-8")).get("status", "")
        except json.JSONDecodeError:
            pass
    if not marker_status and not args.runs_json:
        marker_status = remote_marker_status(os.environ.get("GH_REPO", "LeftSeineM/TrendingAI"), marker_name)
    if args.runs_json:
        runs = json.loads(Path(args.runs_json).read_text(encoding="utf-8"))
    else:
        raw = subprocess.check_output([
            "gh", "run", "list", "--repo", os.environ.get("GH_REPO", "LeftSeineM/TrendingAI"),
            "--workflow", "daily-email.yml", "--limit", "20", "--json",
            "databaseId,event,status,conclusion,createdAt,url",
        ], text=True)
        runs = json.loads(raw)
    action = decide(marker_status, runs, target)
    if action == "sent":
        print(f"DELIVERY_OK marker={now:%Y-%m-%d}-{slug}")
        return
    if action == "running":
        print(f"DELIVERY_WAIT active_run_for={now:%Y-%m-%d}-{slug}")
        return
    issue_path = editorial_issue_path(marker_name)
    if not issue_path.exists():
        print(f"EDITORIAL_MISSING marker={marker_name} path={issue_path}; fallback_will_not_dispatch")
        return
    if args.dry_run:
        print(f"WOULD_DISPATCH edition={slug}")
        return
    subprocess.check_call([
        "gh", "workflow", "run", "daily-email.yml", "--repo",
        os.environ.get("GH_REPO", "LeftSeineM/TrendingAI"), "--ref", "main", "-f", f"edition={slug}",
    ])
    print(f"FALLBACK_DISPATCHED edition={slug}")


if __name__ == "__main__":
    main()
