import hashlib
import json
from pathlib import Path

def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def stable_u64(seed, namespace, *keys):
    text = '|'.join(map(str, (seed, namespace, *keys))).encode()
    return int.from_bytes(hashlib.blake2b(text, digest_size=8).digest(), 'little')

def uniform(seed, namespace, *keys):
    return stable_u64(seed, namespace, *keys) / 2**64

def original_artifact_check(root):
    old = root.parent / 'AIS漏计风险预实验'
    expected = json.loads((old / 'failure_analysis/original_hashes_before.json').read_text(encoding='utf-8'))
    changed = [name for name, digest in expected.items() if sha256(old / name) != digest]
    if changed:
        raise AssertionError(f'original artifacts changed: {changed}')
    return {'files_checked': len(expected), 'all_unchanged': True}

def code_hash(root):
    names = sorted((root / 'src').rglob('*.py')) + sorted((root / 'tests').rglob('*.py'))
    digest = hashlib.sha256()
    for path in names:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
