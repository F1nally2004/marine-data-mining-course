"""Save every test attempt; no overwrite, including failures."""
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
from ais_confirm.audit import code_hash, original_artifact_check

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--label',default='tests')
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=ROOT/'validation'/f'{stamp}_{args.label}'
    output.mkdir(parents=True)
    stream=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    log=stream.getvalue()
    (output/'tests.log').write_text(log,encoding='utf-8')
    summary={'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
             'skipped':len(result.skipped),'passed':result.wasSuccessful(),'code_sha256':code_hash(ROOT),
             'original_artifacts':original_artifact_check(ROOT)}
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(log)
    print(str(output))
    raise SystemExit(0 if result.wasSuccessful() else 1)

if __name__=='__main__':
    main()
