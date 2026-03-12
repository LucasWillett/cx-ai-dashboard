#!/usr/bin/env python3
"""
CX AI Dashboard — Slack intake bot.

Listens for ai-win messages in a channel and adds projects to the dashboard.
Formats:
  /ai-win TourFinder | 7.5 hrs/wk | Property lookups in Slack
  "ai win: CD Bot | 1 hr/wk | ChurnZero property lookups"

Also responds to:
  "ai stats" or "ai dashboard" — posts current totals
"""

import json
import os
import re
import time
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Config
SLACK_BOT_TOKEN = os.environ.get('CX_DASHBOARD_BOT_TOKEN', '')
LISTEN_CHANNELS = {
    'C0AGULNT9EU': 'lucas-bot-testing',
}
DATA_FILE = Path(__file__).parent / 'data.json'
POLL_INTERVAL = 10  # seconds

# Team aliases for matching
TEAM_ALIASES = {
    'exec': 'exec', 'executive': 'exec', 'kevin': 'exec',
    'support': 'support', 'sup': 'support',
    'pmo': 'pmo', 'project management': 'pmo', 'pm': 'pmo',
    'cs': 'cs', 'customer success': 'cs', 'success': 'cs',
}

client = WebClient(token=SLACK_BOT_TOKEN)


def load_data():
    return json.loads(DATA_FILE.read_text())


def save_data(data):
    from datetime import datetime, timezone
    data['meta']['lastUpdated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    DATA_FILE.write_text(json.dumps(data, indent=2))


def parse_ai_win(text):
    """Parse an ai-win message.

    Formats:
      ai win: Tool Name | 5 hrs/wk | Description
      ai win: Tool Name | 30 min/wk | Description
      /ai-win Tool Name | 2.5 hrs/wk | Description
      ai win: Tool Name | 5 hrs/wk | Description | team:support
    """
    # Strip command prefix
    text = re.sub(r'^/?ai[- ]?win:?\s*', '', text, flags=re.IGNORECASE).strip()
    if not text:
        return None

    parts = [p.strip() for p in text.split('|')]
    if len(parts) < 3:
        return None

    name = parts[0]
    time_str = parts[1].lower()
    description = parts[2]

    # Parse time — support "X hrs/wk", "X hr/wk", "X min/wk"
    time_match = re.search(r'([\d.]+)\s*(hrs?|hours?|min|minutes?)', time_str)
    if not time_match:
        return None

    value = float(time_match.group(1))
    unit = time_match.group(2)
    if unit.startswith('min'):
        weekly_minutes = int(value)
    else:
        weekly_minutes = int(value * 60)

    # Optional team tag
    team_id = None
    if len(parts) >= 4:
        team_raw = re.sub(r'^team:\s*', '', parts[3], flags=re.IGNORECASE).strip().lower()
        team_id = TEAM_ALIASES.get(team_raw)

    return {
        'name': name,
        'description': description,
        'weeklyMinutes': weekly_minutes,
        'team': team_id,
    }


def get_org_summary():
    """Get a formatted summary of current dashboard stats."""
    data = load_data()
    lines = []
    org_minutes = 0
    org_projects = 0

    for team in data['teams']:
        team_minutes = sum(p.get('weeklyMinutes', 0) for p in team['projects'])
        team_hours = round(team_minutes / 60, 1)
        count = len(team['projects'])
        org_minutes += team_minutes
        org_projects += count
        lines.append(f"*{team['name']}*: {team_hours} hrs/wk ({count} projects)")

    org_hours = round(org_minutes / 60, 1)
    header = f":chart_with_upwards_trend: *CX AI Impact — {org_hours} hrs/wk saved*\n"
    header += f"_{org_projects} projects across {len(data['teams'])} teams_\n\n"

    return header + '\n'.join(lines)


def add_project_from_slack(parsed, submitter_id):
    """Add a parsed project to the dashboard."""
    data = load_data()

    # If no team specified, try to find submitter in a team's members
    team_id = parsed.get('team')
    if not team_id:
        # Default to support for now — can be smarter later
        team_id = 'support'

    team = next((t for t in data['teams'] if t['id'] == team_id), None)
    if not team:
        return False, f"Unknown team: {team_id}"

    # Dedup
    if any(p['name'].lower() == parsed['name'].lower() for p in team['projects']):
        return False, f"'{parsed['name']}' already exists in {team['name']}"

    team['projects'].append({
        'name': parsed['name'],
        'description': parsed['description'],
        'weeklyMinutes': parsed['weeklyMinutes'],
        'owner': f'<@{submitter_id}>',
        'status': 'production',
        'since': time.strftime('%Y-%m'),
    })

    save_data(data)
    hours = round(parsed['weeklyMinutes'] / 60, 1)
    return True, f"Added *{parsed['name']}* to {team['name']} — {hours} hrs/wk"


def handle_message(event):
    """Process a single message event."""
    text = event.get('text', '').strip()
    channel = event.get('channel')
    user = event.get('user', '')
    ts = event.get('ts', '')

    # Skip bot messages
    if event.get('bot_id') or event.get('subtype'):
        return

    text_lower = text.lower()

    # Stats request
    if text_lower in ('ai stats', 'ai dashboard', 'ai impact', 'ai total'):
        summary = get_org_summary()
        try:
            client.chat_postMessage(channel=channel, text=summary, thread_ts=ts)
        except SlackApiError as e:
            print(f"Error posting stats: {e}")
        return

    # AI win submission
    if re.match(r'/?ai[- ]?win', text_lower):
        parsed = parse_ai_win(text)
        if not parsed:
            try:
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=ts,
                    text=(
                        "Couldn't parse that. Format:\n"
                        "`ai win: Tool Name | 5 hrs/wk | What it does`\n"
                        "Optional team: `ai win: Tool | 2 hrs/wk | Desc | team:pmo`"
                    ),
                )
            except SlackApiError:
                pass
            return

        ok, msg = add_project_from_slack(parsed, user)
        emoji = ':white_check_mark:' if ok else ':warning:'
        try:
            client.chat_postMessage(channel=channel, thread_ts=ts, text=f"{emoji} {msg}")
        except SlackApiError as e:
            print(f"Error posting confirmation: {e}")


def poll_messages():
    """Poll channels for new messages. Simple approach — no socket mode needed."""
    print(f"CX AI Dashboard bot starting — polling {len(LISTEN_CHANNELS)} channel(s)")

    # Track last seen timestamp per channel
    last_ts = {}
    for ch_id in LISTEN_CHANNELS:
        last_ts[ch_id] = str(time.time())

    while True:
        for ch_id, ch_name in LISTEN_CHANNELS.items():
            try:
                resp = client.conversations_history(
                    channel=ch_id,
                    oldest=last_ts[ch_id],
                    limit=20,
                )
                messages = resp.get('messages', [])
                for msg in sorted(messages, key=lambda m: m.get('ts', '0')):
                    if msg.get('ts', '0') > last_ts[ch_id]:
                        handle_message(msg)
                        last_ts[ch_id] = msg['ts']

            except SlackApiError as e:
                if 'not_in_channel' in str(e):
                    print(f"Bot not in #{ch_name} — invite it first")
                else:
                    print(f"Slack error ({ch_name}): {e}")
            except Exception as e:
                print(f"Error polling {ch_name}: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    if not SLACK_BOT_TOKEN:
        print("Set CX_DASHBOARD_BOT_TOKEN environment variable")
        exit(1)
    poll_messages()
