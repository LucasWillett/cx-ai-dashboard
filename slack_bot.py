#!/usr/bin/env python3
"""
CX AI Dashboard — Slack intake bot.

Conversational intake: user says "ai win: Project Name" and the bot
walks them through the details in a thread. No pipe-delimited formats needed.

Also responds to:
  "ai stats" or "ai dashboard" — posts current totals
"""

import json
import os
import random
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

# Map Slack user IDs to teams (populated from data.json members + known IDs)
USER_TEAM_MAP = {
    # Kevin's team
    'U9NM8JFPH': 'exec',       # Kevin Huang
    # Support
    'U9NLNTPDK': 'support',    # Lucas Willett
    'U03NP6HCMJA': 'support',  # Christian Staley
    'U04K118RSLS': 'support',  # Hannah Holbrook
    # PMO — add IDs as they submit
    # CS — add IDs as they submit
}

# Celebration messages — rotating, fun, slightly irreverent
CELEBRATIONS = [
    "Another one for the highlight reel! :basketball:",
    "That's a dagger! Nothing but net :basketball:",
    "You're out here dropping dimes left and right :fire:",
    "AI assist ➡️ bucket. And the crowd goes wild :raised_hands:",
    "Swish. Didn't even touch the rim :basketball:",
    "That's an and-one. Too smooth :sunglasses:",
    "You just posterized that manual process :muscle:",
    "Chef's kiss on that automation :pinching_hand:",
    "The future called — it wants to high-five you :wave:",
    "Efficiency level: over 9000 :zap:",
    "That workflow never saw it coming :boom:",
    "Adding that to the trophy case :trophy:",
    "From downtown... BANG! :basketball:",
    "You're making the robots proud :robot_face:",
    "Manual process in shambles right now :skull:",
    "Steph Curry range on that one :fire:",
    "Triple-double energy. Assists, saves, and vibes :sparkles:",
    "That's going on the scouting report :clipboard:",
    "They're gonna study this play in film review :movie_camera:",
    "Hall of fame efficiency right there :star2:",
]

# Conversation state — tracks in-progress intake threads
# Key: thread_ts, Value: dict with state info
active_intakes = {}

client = WebClient(token=SLACK_BOT_TOKEN)


def load_data():
    return json.loads(DATA_FILE.read_text())


def save_data(data):
    from datetime import datetime, timezone
    data['meta']['lastUpdated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    DATA_FILE.write_text(json.dumps(data, indent=2))


def parse_time_response(text):
    """Parse a freeform time response into weekly minutes.

    Handles: "2 hours", "30 min", "1.5 hrs", "45 minutes a week",
    "about 3 hours", "like 2hrs", "~1 hour", "half hour", "an hour"
    """
    text = text.lower().strip()

    # "half hour" / "half an hour"
    if 'half' in text and 'hour' in text:
        return 30

    # "an hour" / "about an hour"
    if re.search(r'\ban?\s+hour', text):
        return 60

    match = re.search(r'([\d.]+)\s*(hrs?|hours?|min|minutes?|m)\b', text)
    if match:
        value = float(match.group(1))
        unit = match.group(2)
        if unit.startswith('m'):
            return int(value)
        else:
            return int(value * 60)

    # Just a number — assume hours
    num_match = re.search(r'^[~\s]*(\d+\.?\d*)\s*$', text)
    if num_match:
        return int(float(num_match.group(1)) * 60)

    return None


def get_team_for_user(user_id):
    """Look up which team a user belongs to."""
    return USER_TEAM_MAP.get(user_id, None)


def get_team_options():
    """Get formatted team list for the team question."""
    data = load_data()
    return '\n'.join(f"  • *{t['name']}* ({t['lead']}'s team)" for t in data['teams'])


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


def add_project(name, description, weekly_minutes, team_id, submitter_id):
    """Add a project to the dashboard."""
    data = load_data()

    team = next((t for t in data['teams'] if t['id'] == team_id), None)
    if not team:
        return False, f"Unknown team: {team_id}"

    # Dedup
    if any(p['name'].lower() == name.lower() for p in team['projects']):
        return False, f"'{name}' already exists in {team['name']}"

    team['projects'].append({
        'name': name,
        'description': description,
        'weeklyMinutes': weekly_minutes,
        'owner': f'<@{submitter_id}>',
        'status': 'production',
        'since': time.strftime('%Y-%m'),
    })

    save_data(data)
    hours = round(weekly_minutes / 60, 1)
    return True, f"*{name}* → {team['name']} — {hours} hrs/wk"


def post(channel, text, thread_ts=None):
    """Post a message, swallowing errors."""
    try:
        client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
    except SlackApiError as e:
        print(f"Error posting: {e}")


def handle_message(event):
    """Process a single message event."""
    text = event.get('text', '').strip()
    channel = event.get('channel')
    user = event.get('user', '')
    ts = event.get('ts', '')
    thread_ts = event.get('thread_ts')

    # Skip bot messages
    if event.get('bot_id') or event.get('subtype'):
        return

    text_lower = text.lower().strip()

    # --- Thread reply: check if this is part of an active intake ---
    if thread_ts and thread_ts in active_intakes:
        intake = active_intakes[thread_ts]
        if intake['user'] != user:
            return  # Someone else replied in the thread, ignore

        step = intake['step']

        if step == 'description':
            intake['description'] = text
            intake['step'] = 'time'
            post(channel, "Nice. How much time does this save per week? (like \"2 hours\" or \"30 min\")", thread_ts)

        elif step == 'time':
            minutes = parse_time_response(text)
            if minutes is None:
                post(channel, "Hmm, couldn't read that. Try something like \"2 hours\" or \"30 minutes\"", thread_ts)
                return
            intake['weekly_minutes'] = minutes

            # Try to auto-detect team
            team_id = get_team_for_user(user)
            if team_id:
                intake['team'] = team_id
                intake['step'] = 'done'
                _finalize_intake(channel, thread_ts, intake)
            else:
                intake['step'] = 'team'
                team_list = get_team_options()
                post(channel, f"Which team is this for?\n{team_list}", thread_ts)

        elif step == 'team':
            team_id = TEAM_ALIASES.get(text_lower)
            if not team_id:
                # Try fuzzy match on team names
                data = load_data()
                for t in data['teams']:
                    if text_lower in t['name'].lower() or text_lower in t.get('lead', '').lower():
                        team_id = t['id']
                        break
            if not team_id:
                post(channel, "Didn't catch that. Try: support, pmo, cs, or executive", thread_ts)
                return
            intake['team'] = team_id
            intake['step'] = 'done'
            _finalize_intake(channel, thread_ts, intake)

        return

    # --- Top-level messages ---

    # Stats request
    if text_lower in ('ai stats', 'ai dashboard', 'ai impact', 'ai total'):
        summary = get_org_summary()
        post(channel, summary, ts)
        return

    # AI win submission — now just needs the project name
    if re.match(r'/?ai[- ]?win', text_lower):
        # Extract project name — everything after the trigger
        name = re.sub(r'^/?ai[- ]?win:?\s*', '', text, flags=re.IGNORECASE).strip()

        # Strip pipe-delimited format if someone uses the old format (still works)
        if '|' in name:
            _handle_legacy_format(text, channel, user, ts)
            return

        if not name:
            post(channel, "What's the name of the project or tool? Just say `ai win: Project Name`", ts)
            return

        # Start conversational intake in a thread
        active_intakes[ts] = {
            'user': user,
            'name': name,
            'step': 'description',
            'channel': channel,
        }

        post(channel, f"Love it — *{name}*! :eyes:\nWhat does it do? (one sentence is perfect)", ts)
        return


def _handle_legacy_format(text, channel, user, ts):
    """Handle the old pipe-delimited format for power users."""
    raw = re.sub(r'^/?ai[- ]?win:?\s*', '', text, flags=re.IGNORECASE).strip()
    parts = [p.strip() for p in raw.split('|')]
    if len(parts) < 3:
        post(channel, "What's the name of the project or tool? Just say `ai win: Project Name`", ts)
        return

    name = parts[0]
    minutes = parse_time_response(parts[1])
    if minutes is None:
        post(channel, "Couldn't parse the time. Try: `ai win: Tool | 2 hrs/wk | Description`", ts)
        return
    description = parts[2]

    team_id = None
    if len(parts) >= 4:
        team_raw = re.sub(r'^team:\s*', '', parts[3], flags=re.IGNORECASE).strip().lower()
        team_id = TEAM_ALIASES.get(team_raw)
    if not team_id:
        team_id = get_team_for_user(user) or 'support'

    ok, msg = add_project(name, description, minutes, team_id, user)
    if ok:
        celebration = random.choice(CELEBRATIONS)
        post(channel, f":white_check_mark: {msg}\n\n{celebration}", ts)
    else:
        post(channel, f":warning: {msg}", ts)


def _finalize_intake(channel, thread_ts, intake):
    """Save the project and celebrate."""
    ok, msg = add_project(
        intake['name'],
        intake['description'],
        intake['weekly_minutes'],
        intake['team'],
        intake['user'],
    )

    if ok:
        celebration = random.choice(CELEBRATIONS)
        post(channel, f":white_check_mark: {msg}\n\n{celebration}", thread_ts)
    else:
        post(channel, f":warning: {msg}", thread_ts)

    # Clean up
    del active_intakes[thread_ts]


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
                # Get top-level messages
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

                # Check active intake threads for replies
                for thread_ts in list(active_intakes.keys()):
                    intake = active_intakes[thread_ts]
                    if intake['channel'] != ch_id:
                        continue
                    try:
                        thread_resp = client.conversations_replies(
                            channel=ch_id,
                            ts=thread_ts,
                            oldest=thread_ts,
                            limit=20,
                        )
                        thread_msgs = thread_resp.get('messages', [])
                        for msg in sorted(thread_msgs, key=lambda m: m.get('ts', '0')):
                            # Skip the parent message and bot messages
                            if msg.get('ts') == thread_ts:
                                continue
                            if msg.get('bot_id') or msg.get('subtype'):
                                continue
                            # Only process if newer than our last question
                            if not intake.get('last_processed') or msg['ts'] > intake['last_processed']:
                                intake['last_processed'] = msg['ts']
                                msg['thread_ts'] = thread_ts
                                handle_message(msg)
                    except SlackApiError:
                        pass

            except SlackApiError as e:
                if 'not_in_channel' in str(e):
                    print(f"Bot not in #{ch_name} — invite it first")
                else:
                    print(f"Slack error ({ch_name}): {e}")
            except Exception as e:
                print(f"Error polling {ch_name}: {e}")

        # Clean up stale intakes (older than 30 min)
        now = time.time()
        for ts in list(active_intakes.keys()):
            if now - float(ts) > 1800:
                del active_intakes[ts]

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    if not SLACK_BOT_TOKEN:
        print("Set CX_DASHBOARD_BOT_TOKEN environment variable")
        exit(1)
    poll_messages()
