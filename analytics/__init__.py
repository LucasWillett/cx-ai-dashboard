"""VMP Distribution Analytics — Flask Blueprint.

Originally lived as a standalone service at truetour-analytics.onrender.com.
Merged into cx-ai-dashboard 2026-05-26 to consolidate Render billing.

Data files (current.json, tt8_retirement.json) are written locally by
truetour-analytics/collector.py and mirrored into this package via git push.
"""
import json
from pathlib import Path

from flask import Blueprint, send_from_directory, jsonify, request

PACKAGE_DIR = Path(__file__).parent
STATIC_DIR = PACKAGE_DIR / 'static'
DATA_DIR = PACKAGE_DIR / 'data'
CURRENT_DATA = DATA_DIR / 'current.json'
TT8_RETIREMENT_DATA = DATA_DIR / 'tt8_retirement.json'

analytics_bp = Blueprint(
    'analytics',
    __name__,
    static_folder='static',
    static_url_path='/static',
)


def load_data():
    if CURRENT_DATA.exists():
        return json.loads(CURRENT_DATA.read_text())
    return {'error': 'No data available. Run collector.py first.'}


@analytics_bp.route('/')
def dashboard():
    return send_from_directory(str(STATIC_DIR), 'index.html')


@analytics_bp.route('/api/data')
def full_data():
    return jsonify(load_data())


@analytics_bp.route('/api/summary')
def summary():
    data = load_data()
    if 'error' in data:
        return jsonify(data), 503
    return jsonify({
        'generated_at': data['generated_at'],
        'period': data['period'],
        'totals': data['totals'],
        'categories': data.get('categories', {}),
        'platform_breakdown': data.get('platform_breakdown', {}),
        'alerts': data.get('alerts', []),
    })


@analytics_bp.route('/api/channels/<category>')
def channels_by_category(category):
    data = load_data()
    if 'error' in data:
        return jsonify(data), 503
    return jsonify(data.get(category, []))


@analytics_bp.route('/api/trends')
def trends():
    data = load_data()
    if 'error' in data:
        return jsonify(data), 503
    return jsonify({
        'alerts': data.get('alerts', []),
        'weekly': data.get('weekly', {}),
    })


@analytics_bp.route('/api/properties')
def properties():
    data = load_data()
    if 'error' in data:
        return jsonify(data), 503
    brand = request.args.get('brand')
    channel = request.args.get('channel')
    limit = int(request.args.get('limit', 30))

    props = data.get('top_properties', [])
    if brand:
        props = [p for p in props if brand.lower() in p.get('brand', '').lower()]
    if channel:
        props = [p for p in props if channel in p.get('channels', {})]
    return jsonify(props[:limit])


@analytics_bp.route('/api/tt8-retirement')
def tt8_retirement():
    if TT8_RETIREMENT_DATA.exists():
        return jsonify(json.loads(TT8_RETIREMENT_DATA.read_text()))
    return jsonify({'error': 'No tt8 retirement data yet.'}), 503
