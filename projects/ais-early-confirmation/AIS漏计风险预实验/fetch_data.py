"""Download a pinned, ~4.7 MiB public AISdb tutorial database, then export points."""
import argparse
import hashlib
import json
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = 'MAPS-Lab/AISdb-Tutorials'
COMMIT = 'bb978e03a8469184e421cec8927aae49aacfe8c7'
EXPECTED_SHA256 = 'f90b3dc64f02a879177d25e20a8266c803e098fce664971e24ed2b89687fd72e'


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'AIS-distinct-pilot/1.0'})
    with urllib.request.urlopen(req, timeout=60) as response:
        data = response.read(12*1024**2+1)
    if len(data) > 12*1024**2: raise RuntimeError('Sample download exceeds 12 MiB limit')
    return data


def acquire():
    data_dir = ROOT/'data'
    data_dir.mkdir(exist_ok=True)
    db = data_dir/'example_data.db'
    manifest_path = data_dir/'source_manifest.json'
    if not db.exists():
        commit = COMMIT
        url = f'https://raw.githubusercontent.com/{REPO}/{commit}/example_data.db'
        body = get(url)
        if not body.startswith(b'SQLite format 3'): raise RuntimeError('Not a SQLite database')
        if hashlib.sha256(body).hexdigest() != EXPECTED_SHA256: raise RuntimeError('Pinned source hash mismatch')
        db.write_bytes(body)
        for name in ('README.md', 'LICENSE', 'CITATION.cff'):
            (data_dir/('source_'+name)).write_bytes(get(f'https://raw.githubusercontent.com/{REPO}/{commit}/{name}'))
        manifest = {'repository': f'https://github.com/{REPO}', 'commit': commit,
                    'url': url, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                    'scope': 'Tutorial-selected Gulf of Maine AIS sample; not a complete population',
                    'source_time': 'Database time reused as replay event time, not proven ship transmit time'}
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert hashlib.sha256(db.read_bytes()).hexdigest() == manifest['sha256']
    assert manifest['sha256'] == EXPECTED_SHA256
    return db


def export(db, inspect_only=False):
    with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as con:
        tables = con.execute("SELECT name,sql FROM sqlite_master WHERE type='table'").fetchall()
        if inspect_only:
            for name, sql in tables:
                print(name, sql)
                print(con.execute(f'SELECT * FROM "{name}" LIMIT 2').fetchall())
            return
        frames = []
        for name, _ in tables:
            if name.endswith('_dynamic'):
                frames.append(pd.read_sql_query(f'SELECT mmsi,time,longitude,latitude FROM "{name}"', con))
    if not frames: raise RuntimeError('No supported *_dynamic point table')
    df = pd.concat(frames, ignore_index=True)
    df.columns = ['mmsi', 'event_time', 'lon', 'lat']
    df['event_time'] = pd.to_datetime(df.event_time, unit='s', utc=True)
    df = df.dropna().loc[lambda d: d.mmsi.between(100000000,999999999) & d.lat.between(-90,90) & d.lon.between(-180,180)]
    df = df.drop_duplicates().sort_values(['event_time','mmsi','lat','lon']).reset_index(drop=True)
    df['event_id'] = range(len(df))
    df.to_parquet(ROOT/'data/events.parquet', index=False)
    info = {'rows': len(df), 'ships': int(df.mmsi.nunique()), 'start': str(df.event_time.min()),
            'end': str(df.event_time.max()), 'by_day': df.groupby(df.event_time.dt.strftime('%Y-%m-%d')).size().to_dict(),
            'bounds': [float(df.lon.min()),float(df.lat.min()),float(df.lon.max()),float(df.lat.max())]}
    (ROOT/'data/data_summary.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    print(json.dumps(info, indent=2), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--inspect', action='store_true')
    args = p.parse_args()
    export(acquire(), args.inspect)
