"""Pinned college weekly release reader; no recomputation or live inputs."""
import hashlib
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
COLLEGE = ROOT / 'data/college'
PINS = {
 '2026/week-1/v1.1': '0553346e90791eed1ae593521753a121f9deffe1e79df917d07d79ba150ea3ce',
 '2026/week-2/v1.0': '4969c96d5115630e14e644f75e565f8eb152f02e912f2825db325d908916447e',
}


def active_release():
    return json.loads((COLLEGE/'config.json').read_text())['activeCollegeWeeklyProjectionVersion']


def load_release(version=None):
    version = version or active_release()
    release = COLLEGE / version
    raw = (release/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINS.get(version):
        raise ValueError('College weekly manifest does not match pinned release')
    manifest = json.loads(raw)
    if manifest.get('qa_status') != 'PASS':
        raise ValueError('College weekly QA incomplete')
    for name, expected in manifest['files'].items():
        path = release/name
        if not path.is_file():
            path = release/'provenance'/name
        data = path.read_bytes()
        if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise ValueError(f'College weekly artifact mismatch: {name}')
    week = int(version.split('/')[1].split('-')[1])
    data = json.loads((release/f'college_week{week}_site_projections_2026.json').read_text())
    if data['week'] != week or data['season'] != 2026 or len(data['players']) != data['counts']['players']:
        raise ValueError('College weekly scope mismatch')
    return release, data
