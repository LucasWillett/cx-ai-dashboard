#!/usr/bin/env python3
"""CX AI Impact Dashboard — Kevin Huang's CX-wide AI time savings tracker."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_file

app = Flask(__name__)
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

    team['projects'].append({
        'name': payload['name'],
        'description': payload['description'],
        'weeklyMinutes': int(payload['weeklyMinutes']),
        'owner': payload['owner'],
        'status': payload.get('status', 'production'),
        'since': datetime.now(timezone.utc).strftime('%Y-%m'),
        'addedDate': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5015))
    app.run(host='0.0.0.0', port=port, debug=True)
