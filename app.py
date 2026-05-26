#!/usr/bin/env python3
"""CX AI Impact Dashboard — Kevin Huang's CX-wide AI time savings tracker.

Also serves the /ai-win Slack slash command (consolidated from ai-in-action-slash).
Slack app config:
  - Slash Command: /ai-win → https://<render-url>/slack/ai-win
  - Interactivity Request URL: https://<render-url>/slack/interact
"""

import hashlib
import hmac
import json
import os
import random
import re
import time as _time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

app = Flask(__name__)

# --- Slack config ---
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / 'data.json'
GROWTH_FILE = BASE_DIR / 'growth.json'
DASHBOARD_FILE = BASE_DIR / 'demo.html'


def load_data():
    return json.loads(DATA_FILE.read_text())


def load_growth():
    if GROWTH_FILE.exists():
        return json.loads(GROWTH_FILE.read_text())
    return []


def save_data(data):
    data['meta']['lastUpdated'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    DATA_FILE.write_text(json.dumps(data, indent=2))


def compute_stats(data):
    """Compute per-team and org-wide stats."""
    teams = []
    org_total_minutes = 0
    org_total_projects = 0

    for team in data['teams']:
        weekly_minutes = sum(p.get('weeklyMinutes', 0) for p in team['projects'])
        project_count = len(team['projects'])
        production_count = sum(1 for p in team['projects'] if p.get('status') == 'production')

        teams.append({
            **team,
            'weeklyMinutes': weekly_minutes,
            'weeklyHours': round(weekly_minutes / 60, 1),
            'projectCount': project_count,
            'productionCount': production_count,
        })

        org_total_minutes += weekly_minutes
        org_total_projects += project_count

    return {
        'teams': teams,
        'org': {
            'weeklyMinutes': org_total_minutes,
            'weeklyHours': round(org_total_minutes / 60, 1),
            'monthlyHours': round(org_total_minutes / 60 * 4.33, 1),
            'annualHours': round(org_total_minutes / 60 * 52, 1),
            'projectCount': org_total_projects,
        },
        'meta': data['meta'],
    }


@app.route('/')
def dashboard():
    return send_file(DASHBOARD_FILE)


@app.route('/api/stats')
def api_stats():
    data = load_data()
    return jsonify(compute_stats(data))


@app.route('/api/data')
def api_data():
    """Raw data.json — consumed by the dashboard client-side."""
    return jsonify(load_data())


@app.route('/api/growth')
def api_growth():
    """Growth timeline data."""
    return jsonify(load_growth())


@app.route('/api/add-project', methods=['POST'])
def add_project():
    """Add a new project to a team. Used by Slack bot or manual entry."""
    payload = request.json
    required = ['team', 'name', 'description', 'weeklyMinutes', 'owner']
    if not all(k in payload for k in required):
        return jsonify({'error': f'Missing fields. Required: {required}'}), 400

    data = load_data()
    team = next((t for t in data['teams'] if t['id'] == payload['team']), None)
    if not team:
        return jsonify({'error': f"Unknown team: {payload['team']}"}), 400

    # Dedup by name
    if any(p['name'].lower() == payload['name'].lower() for p in team['projects']):
        return jsonify({'error': f"Project '{payload['name']}' already exists in {team['name']}"}), 409

    project = {
        'name': payload['name'],
        'description': payload['description'],
        'weeklyMinutes': int(payload['weeklyMinutes']),
        'owner': payload['owner'],
        'status': payload.get('status', 'production'),
        'since': datetime.now(timezone.utc).strftime('%Y-%m'),
        'addedDate': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
    }
    # Enriched fields from intake bot
    for field in ['frequency', 'rawMinutes', 'confluenceUrl']:
        if payload.get(field):
            project[field] = payload[field]

    team['projects'].append(project)

    # Activity log
    activity = data.setdefault('activity', [])
    activity.insert(0, {
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'event': f"{payload['name']} — {payload['description'][:100]}",
        'type': 'deploy',
        'contributor': payload['owner'],
        'team': payload['team'],
    })

    save_data(data)
    return jsonify({'ok': True, 'message': f"Added '{payload['name']}' to {team['name']}"})


@app.route('/api/update-project', methods=['POST'])
def update_project():
    """Update an existing project's time savings."""
    payload = request.json
    if not payload.get('team') or not payload.get('name'):
        return jsonify({'error': 'team and name required'}), 400

    data = load_data()
    team = next((t for t in data['teams'] if t['id'] == payload['team']), None)
    if not team:
        return jsonify({'error': f"Unknown team: {payload['team']}"}), 400

    project = next((p for p in team['projects'] if p['name'].lower() == payload['name'].lower()), None)
    if not project:
        return jsonify({'error': f"Project '{payload['name']}' not found in {team['name']}"}), 404

    for field in ['description', 'weeklyMinutes', 'owner', 'status']:
        if field in payload:
            project[field] = int(payload[field]) if field == 'weeklyMinutes' else payload[field]

    save_data(data)
    return jsonify({'ok': True, 'message': f"Updated '{payload['name']}'"})


# ---------------------------------------------------------------------------
# /ai-win slash command (consolidated from ai-in-action-slash)
# ---------------------------------------------------------------------------

TEAM_OPTIONS = [
    {"text": {"type": "plain_text", "text": "Support"}, "value": "support"},
    {"text": {"type": "plain_text", "text": "Customer Success"}, "value": "cs-ryan"},
    {"text": {"type": "plain_text", "text": "PMO"}, "value": "pmo"},
]

FREQUENCY_OPTIONS = [
    {"text": {"type": "plain_text", "text": "Weekly"}, "value": "weekly"},
    {"text": {"type": "plain_text", "text": "Monthly"}, "value": "monthly"},
    {"text": {"type": "plain_text", "text": "One-time"}, "value": "one-time"},
]

USER_TEAM_MAP = {
    "U9NLNTPDK": "exec",
    "U03NP6HCMJA": "support",
    "U04K118RSLS": "support",
    "U01572F2Z8U": "exec",
    "UNZ4YMDR9": "pmo",
}

KNOWN_USERS = {
    "U9NLNTPDK": "Lucas Willett",
    "U03NP6HCMJA": "Christian Staley",
    "U04K118RSLS": "Hannah Holbrook",
    "U01572F2Z8U": "Ryan Schwartz",
    "UNZ4YMDR9": "Jackie George",
}

CELEBRATIONS = [
    ":blob-wave: Another one on the board!",
    ":meow_salute: Logged and locked. Nice work.",
    ":party_blob: That's what we're talking about!",
    ":blob-hearts: The board just got better.",
    ":meow_heart: Shipped and scored.",
    ":blob_cozy: Cozy win. Love it.",
    ":meow-hehehe: Sneaky good. Noted.",
    ":meow_detective: Case closed. Win recorded.",
    ":blob-heart: Heart of a builder.",
    ":meow_this_is_fine: Everything is fine. Better than fine.",
]


def _slack_client():
    return WebClient(token=SLACK_TOKEN)


def _verify_slack_request(req):
    if not SLACK_SIGNING_SECRET:
        return True
    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    if abs(_time.time() - int(timestamp)) > 60 * 5:
        return False
    sig_basestring = f"v0:{timestamp}:{req.get_data(as_text=True)}"
    my_sig = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    slack_sig = req.headers.get("X-Slack-Signature", "")
    return hmac.compare_digest(my_sig, slack_sig)


def _get_user_name(user_id):
    if user_id in KNOWN_USERS:
        return KNOWN_USERS[user_id]
    try:
        resp = _slack_client().users_info(user=user_id)
        profile = resp["user"]["profile"]
        return profile.get("real_name") or profile.get("display_name") or "Unknown"
    except SlackApiError:
        return "Unknown"


def _parse_time(text):
    text = text.lower().strip()
    m = re.search(r'(\d+)\s*min', text)
    if m:
        return int(m.group(1))
    if 'half hour' in text or 'half an hour' in text:
        return 30
    if text in ('an hour', '1 hour', '1 hr', 'one hour'):
        return 60
    m = re.search(r'(\d+\.?\d*)\s*h(?:ou)?rs?', text)
    if m:
        return int(float(m.group(1)) * 60)
    m = re.match(r'^(\d+)$', text)
    if m:
        val = int(m.group(1))
        return val if val > 10 else val * 60
    return None


def _build_modal(initial_text=""):
    initial_name = initial_text.strip() if initial_text else ""
    return {
        "type": "modal",
        "callback_id": "ai_win_submit",
        "title": {"type": "plain_text", "text": "Log an AI Win"},
        "submit": {"type": "plain_text", "text": "Submit"},
        "blocks": [
            {
                "type": "input",
                "block_id": "project_name",
                "label": {"type": "plain_text", "text": "Project Name"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "name_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. Triage Buddy"},
                    **({"initial_value": initial_name} if initial_name else {}),
                },
            },
            {
                "type": "input",
                "block_id": "description",
                "label": {"type": "plain_text", "text": "What does it do?"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "desc_input",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "One sentence: what problem does this solve?"},
                },
            },
            {
                "type": "input",
                "block_id": "time_saved",
                "label": {"type": "plain_text", "text": "Time saved"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "time_input",
                    "placeholder": {"type": "plain_text", "text": "e.g. 2 hours, 30 min, 0.5 hrs"},
                },
            },
            {
                "type": "input",
                "block_id": "frequency",
                "label": {"type": "plain_text", "text": "How often?"},
                "element": {
                    "type": "static_select",
                    "action_id": "freq_select",
                    "options": FREQUENCY_OPTIONS,
                    "initial_option": FREQUENCY_OPTIONS[0],
                },
            },
            {
                "type": "input",
                "block_id": "team",
                "label": {"type": "plain_text", "text": "Team"},
                "element": {
                    "type": "static_select",
                    "action_id": "team_select",
                    "options": TEAM_OPTIONS,
                },
                "optional": True,
            },
            {
                "type": "input",
                "block_id": "confluence",
                "label": {"type": "plain_text", "text": "Confluence link (optional)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "confluence_input",
                    "placeholder": {"type": "plain_text", "text": "https://visiting-media.atlassian.net/..."},
                },
                "optional": True,
            },
        ],
    }


def _save_win(*, name, description, weekly_minutes, raw_minutes, frequency,
              team, user_id, user_name, confluence_url, channel_id):
    """Save project directly to local data (no HTTP round-trip)."""
    data = load_data()
    team_obj = next((t for t in data['teams'] if t['id'] == team), None)
    if not team_obj:
        print(f"Unknown team '{team}' for /ai-win submission")
        return

    if any(p['name'].lower() == name.lower() for p in team_obj['projects']):
        print(f"Duplicate project '{name}' in {team}")
        return

    project = {
        'name': name,
        'description': description,
        'weeklyMinutes': weekly_minutes,
        'owner': user_name,
        'status': 'production',
        'since': datetime.now(timezone.utc).strftime('%Y-%m'),
        'addedDate': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'frequency': frequency,
        'rawMinutes': raw_minutes,
    }
    if confluence_url:
        project['confluenceUrl'] = confluence_url

    team_obj['projects'].append(project)

    activity = data.setdefault('activity', [])
    activity.insert(0, {
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'event': f"{name}: {description[:100]}",
        'type': 'deploy',
        'contributor': user_name,
        'team': team,
    })

    save_data(data)

    if channel_id:
        _post_celebration(channel_id, name, description, weekly_minutes, raw_minutes,
                          frequency, team, user_id)


def _post_celebration(channel_id, name, description, weekly_minutes, raw_minutes,
                      frequency, team, user_id):
    hours = weekly_minutes / 60
    raw_hrs = raw_minutes / 60

    if frequency == "one-time":
        time_label = f"{raw_hrs:.1f} hrs saved (one-time)"
    elif frequency == "monthly":
        time_label = f"{raw_hrs:.1f} hrs/month"
    else:
        time_label = f"{hours:.1f} hrs/week"

    celebrate_emoji = random.choice([
        ":trophy:", ":first_place_medal:", ":rocket:",
        ":star2:", ":dart:", ":muscle:", ":fire:", ":medal:",
        ":chart_with_upwards_trend:", ":sparkles:", ":raised_hands:",
    ])

    public_text = (
        f"{celebrate_emoji} *New AI Win: {name}*\n\n"
        f"_{description[:150]}_\n\n"
        f"*{time_label}* for Team {team.upper()} -- hat tip to <@{user_id}>\n"
        f"<https://cx-ai-dashboard.onrender.com|See all wins on the dashboard>"
    )

    try:
        _slack_client().chat_postMessage(channel=channel_id, text=public_text)
    except SlackApiError as e:
        print(f"Celebration post failed: {e}")


@app.route("/slack/interact", methods=["GET", "OPTIONS"])
@app.route("/slack/ai-win", methods=["GET", "OPTIONS"])
def slack_verification():
    return "ok", 200


@app.route("/slack/ai-win", methods=["POST"])
def handle_slash_command():
    if not _verify_slack_request(request):
        return "Invalid request", 403

    trigger_id = request.form.get("trigger_id")
    initial_text = request.form.get("text", "")
    user_id = request.form.get("user_id", "")
    channel_id = request.form.get("channel_id", "")

    modal = _build_modal(initial_text)
    modal["private_metadata"] = json.dumps({"channel_id": channel_id, "user_id": user_id})

    try:
        _slack_client().views_open(trigger_id=trigger_id, view=modal)
    except SlackApiError as e:
        print(f"Modal open failed: {e}")
        return jsonify({"response_type": "ephemeral", "text": f"Failed to open form: {e}"}), 200

    return "", 200


@app.route("/slack/interact", methods=["POST"])
def handle_interaction():
    payload = json.loads(request.form.get("payload", "{}"))

    if payload.get("type") != "view_submission":
        return "", 200
    if payload.get("view", {}).get("callback_id") != "ai_win_submit":
        return "", 200

    view = payload["view"]
    values = view["state"]["values"]
    user_id = payload["user"]["id"]
    meta = json.loads(view.get("private_metadata", "{}"))
    channel_id = meta.get("channel_id", "")

    name = values["project_name"]["name_input"]["value"].strip()
    description = values["description"]["desc_input"]["value"].strip()
    time_text = values["time_saved"]["time_input"]["value"].strip()
    freq = values["frequency"]["freq_select"]["selected_option"]["value"]

    team_block = values["team"]["team_select"].get("selected_option")
    team = team_block["value"] if team_block else USER_TEAM_MAP.get(user_id)

    confluence = values.get("confluence", {}).get("confluence_input", {}).get("value", "")

    raw_minutes = _parse_time(time_text)
    if not raw_minutes:
        return jsonify({
            "response_action": "errors",
            "errors": {"time_saved": "Couldn't parse that. Try '30 min' or '2 hours'."}
        })

    if not team:
        return jsonify({
            "response_action": "errors",
            "errors": {"team": "Please select your team."}
        })

    if freq == "one-time":
        weekly_minutes = round(raw_minutes / 52)
    elif freq == "monthly":
        weekly_minutes = round(raw_minutes / 4.33)
    else:
        weekly_minutes = raw_minutes

    user_name = _get_user_name(user_id)
    _save_win(
        name=name,
        description=description,
        weekly_minutes=weekly_minutes,
        raw_minutes=raw_minutes,
        frequency=freq,
        team=team,
        user_id=user_id,
        user_name=user_name,
        confluence_url=confluence or None,
        channel_id=channel_id,
    )

    return "", 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5015))
    app.run(host='0.0.0.0', port=port, debug=True)
