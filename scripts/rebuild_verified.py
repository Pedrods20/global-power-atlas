"""Rebuild active history from cached, timestamped provider responses.

Run with the repository interpreter. Writes only to .gpa/audited-data; promotion
is separate so an interrupted provider request cannot replace the live store.
Re-running resumes the same response snapshot. Start a new cache directory to
audit a different provider vintage.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path

from gpa import store
from gpa.schema import validate
from gpa.sources import ons
from gpa.sources.energy_charts import EnergyChartsSource
from gpa.zones import ZONES

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.gpa/provider-audit'
OUTPUT = ROOT / '.gpa/audited-data'
CACHE.mkdir(parents=True, exist_ok=True)
os.environ['GPA_DATA_ROOT'] = str(OUTPUT)
manifest_path = CACHE / 'manifest.json'
manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
    'started_at': dt.datetime.now(dt.UTC).isoformat(),
    'start': '2024-09-01T00:00:00+00:00',
    'end': dt.datetime.now(dt.UTC).isoformat(),
    'responses': {},
}


def cached(key, fetch):
    digest = hashlib.sha256(key.encode()).hexdigest()
    path = CACHE / f'{digest}.json'
    if path.exists() and key in manifest['responses']:
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == manifest['responses'][key]['sha256']
        return json.loads(raw)
    time.sleep(0.5)
    value = fetch()
    raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
    path.write_bytes(raw)
    manifest['responses'][key] = {'retrieved_at': dt.datetime.now(dt.UTC).isoformat(),
                                  'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return value


ec = EnergyChartsSource()
original_get = ec._get
ec._get = lambda path, params: cached(
    'https://api.energy-charts.info' + path + '?' + json.dumps(params, sort_keys=True),
    lambda: original_get(path, params),
)
original_text, original_json = ons.fetch_text, ons.fetch_json
ons.fetch_text = lambda url, **kwargs: cached(url, lambda: original_text(url, **kwargs))
ons.fetch_json = lambda url, **kwargs: cached(
    url + '?' + json.dumps(kwargs.get('params', {}), sort_keys=True),
    lambda: original_json(url, **kwargs),
)
start, end = (dt.datetime.fromisoformat(manifest[k]) for k in ('start', 'end'))
for zone in ZONES:
    source = ec if zone.code != 'BR-SIN' else ons.OnsSource()
    for dataset in zone.sources:
        cursor = start
        while cursor < end:
            stop = min((cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1), end)
            frame = validate(source.fetch(zone, dataset, cursor, stop), dataset)
            store.write(frame, dataset)
            print(f'{zone.code} {dataset} {cursor.date()} {len(frame):,}', flush=True)
            cursor = stop
manifest['completed_at'] = dt.datetime.now(dt.UTC).isoformat()
manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('COMPLETE', store.coverage().select('zone', 'dataset', 'rows'), flush=True)
