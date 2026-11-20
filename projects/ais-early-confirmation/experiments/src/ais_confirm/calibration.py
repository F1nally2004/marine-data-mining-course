"""Freeze measured development-only CPU costs before replay; never tune on outcomes."""
import hashlib
import json
import platform
import statistics
import struct
import sys
import time
from pathlib import Path

def calibrate(path):
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    samples = {k: [] for k in ['set_key', 'sort_key', 'serialize_byte', 'bloom_key', 'modmul']}
    detail = []
    for n in [1000, 10000]:
        keys = [200000000 + (i * 7919 % n) for i in range(n)]
        for repeat in range(7):
            operations = {
                'set_key': (lambda: set(keys), n),
                'sort_key': (lambda: sorted(set(keys)), n),
                'serialize_byte': (lambda: struct.pack('<' + 'I'*n, *keys), n*4),
                'bloom_key': (lambda: [hashlib.blake2b(struct.pack('<I', k), digest_size=16).digest() for k in keys], n),
                'modmul': (lambda: _product(keys), n),
            }
            for name, (operation, denominator) in operations.items():
                start = time.perf_counter_ns()
                operation()
                elapsed = (time.perf_counter_ns() - start) / 1000
                samples[name].append(elapsed / denominator)
                detail.append({'operation': name, 'n': n, 'repeat': repeat, 'total_us': elapsed})
    result = {'schema_version': 1, 'status': 'frozen_before_protocol_replay',
              'python': sys.version, 'platform': platform.platform(),
              'costs_us': {k: statistics.median(v) for k, v in samples.items()},
              'method': 'median of 7 repetitions at each of 1000/10000 synthetic keys; additive per-unit CPU approximation',
              'limitations': 'CPU model includes measured operation costs; heap scheduling and Python control flow are measured in wall/CPU runtime but not all charged to simulated endpoint CPUs.',
              'samples': detail}
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    return result

def _product(keys):
    value, p, r = 1, 2**127-1, 123456789012345678901234567890
    for key in set(keys):
        value = value * (r-key) % p
    return value

def hash_supplement(path):
    """Add a measured bit-probe cost; preserve all originally frozen exact-stage costs."""
    path=Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    values=[]
    for n in [1000,10000]:
        positions=[(i*7919)%100003 for i in range(n)]
        for _ in range(7):
            data=bytearray(12501)
            started=time.perf_counter_ns()
            for bit in positions:
                data[bit//8] |= 1 << (bit%8)
            for bit in positions:
                bool(data[bit//8] & (1 << (bit%8)))
            values.append((time.perf_counter_ns()-started)/1000/(2*n))
    result={'status':'frozen_before_hash_replay','additive_only':True,
            'costs_us':{'bloom_probe':statistics.median(values)},'samples_us_per_probe':values,
            'note':'No existing CPU coefficient changed. Bit probes were not used by exact protocols.'}
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    print(json.dumps(calibrate(root/'calibration.json')['costs_us'], indent=2))
