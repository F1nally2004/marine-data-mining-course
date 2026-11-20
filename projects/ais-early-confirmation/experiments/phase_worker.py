"""Execute disjoint pre-existing matrix rows; no changes to experimental parameters.

Used to parallelize pressure/fault phases while the sequential --resume driver
executes replay. The driver skips completed rows. Never touches another active
directory, and does not overwrite the global selection.json.
"""
import argparse
import csv
import gc
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from ais_confirm.runner import run_one
from ais_confirm.audit import code_hash

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',choices=['replay','pressure','faults'],required=True)
    parser.add_argument('--case',action='append')
    parser.add_argument('--method',action='append')
    parser.add_argument('--minimum-matrix-position',type=int,default=1)
    parser.add_argument('--output',default='outputs/matrix_v1')
    args=parser.parse_args()
    cfg=json.loads((ROOT/'experiment_config.json').read_text(encoding='utf-8'))
    base=json.loads((ROOT/'calibration.json').read_text(encoding='utf-8'))
    extra=json.loads((ROOT/'hash_calibration.json').read_text(encoding='utf-8'))
    calibration={**base,'costs_us':{**base['costs_us'],**extra['costs_us']}}
    with (ROOT/'run_matrix.csv').open(encoding='utf-8-sig',newline='') as h:
        specs=[r for pos,r in enumerate(csv.DictReader(h),1) if pos>=args.minimum_matrix_position
               and r['phase']==args.phase and (not args.case or r['case_id'] in args.case)
               and (not args.method or r['method'] in args.method)]
    output=ROOT/args.output
    for index,spec in enumerate(specs,1):
        path=output/spec['run_id']
        if path.exists():
            if not (path/'audit.json').exists():
                raise RuntimeError(f'active or failed run directory; leaving it untouched: {path}')
            audit=json.loads((path/'audit.json').read_text(encoding='utf-8'))
            assert audit['code_sha256']==code_hash(ROOT)
            continue
        result=run_one(spec,cfg,calibration,path)
        print(json.dumps({'worker_phase':args.phase,'case':spec['case_id'],'index':index,'of':len(specs),
                          'run_id':spec['run_id'],'method':spec['method'],
                          **{k:result[k] for k in ['confirmed_windows','ever_false_count_windows','wall_seconds']}},ensure_ascii=False),flush=True)
        gc.collect()

if __name__=='__main__':
    main()
