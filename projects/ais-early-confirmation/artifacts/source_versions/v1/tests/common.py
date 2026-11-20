import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ais_confirm.engine import Engine

def config(n=2):
    cfg = json.loads((ROOT / 'experiment_config.json').read_text(encoding='utf-8'))
    cfg['sources']['count'] = n
    cfg['sources']['centers_lon_lat'] = cfg['sources']['centers_lon_lat'][:n]
    return cfg

def engine():
    return Engine(dict(set_key=.1, serialize_byte=.01, sort_key=.1, bloom_key=.1, bloom_probe=.1, modmul=.1))
