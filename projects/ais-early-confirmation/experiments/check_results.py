"""Offline validation; never modifies any run output."""
import argparse
import csv
import json
from pathlib import Path
import sys
import pandas as pd
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from ais_confirm.audit import sha256, original_artifact_check
from ais_confirm.schema import FINAL, WINDOW, SECOND

def check(output,overrides=None):
    results=[]
    for folder in sorted(output.iterdir()):
        if not folder.is_dir():
            continue
        if overrides is not None and (overrides/folder.name/'audit.json').exists():
            folder=overrides/folder.name
        if not (folder/'audit.json').exists():
            results.append({'run_id':folder.name,'status':'incomplete_or_failed'})
            continue
        audit=json.loads((folder/'audit.json').read_text(encoding='utf-8'))
        summary=json.loads((folder/'run_metrics.json').read_text(encoding='utf-8'))
        for name,digest in audit['output_sha256'].items():
            assert sha256(folder/name)==digest,(folder,name,'hash mismatch')
        pubs=pd.read_parquet(folder/'publications.parquet')
        truth=pd.read_parquet(folder/'truth_captured.parquet').set_index('window').distinct_count.to_dict()
        metrics=pd.read_csv(folder/'window_metrics.csv')
        assert len(metrics)==summary['windows']
        assert metrics.first_publication_latency.eq(0).all()
        assert pubs.groupby('window').version.apply(lambda x:x.is_monotonic_increasing).all()
        nominal=summary['case_id'] in {'base','n1000_r1','n1000_r5','n10000_r1','n10000_r5'}
        exact=pubs[(pubs.status=='EXACT_FINAL') & pubs.window.isin(truth)]
        if nominal:
            assert all(int(r.count)==truth[r.window] for r in exact.itertuples()),folder
            assert summary['coordinator_audit']['contract_violations']==0,folder
            if summary['method'] not in {'WM_DELTA','TIMER_DELTA'}:
                assert summary['ever_false_count_windows']==0,folder
        if summary['case_id']=='missing_seal' and summary['method'] not in {'WM_DELTA','TIMER_DELTA'}:
            assert summary['confirmed_windows']==0,folder
        assert metrics.hash_error_budget.le(1e-18).all(),folder
        net=pd.read_parquet(folder/'message_log.parquet')
        assert net.application_bytes.add(40).eq(net.network_bytes).all()
        assert net.application_bytes.le(1200).all()
        for _, link in net.dropna(subset=['tx_start']).groupby('source'):
            order=link.sort_values('tx_start')
            assert (order.tx_start.to_numpy()[1:]>=order.tx_end.to_numpy()[:-1]).all(),folder
        results.append({'run_id':folder.name,'status':'checked',
                        'result_path':str(folder),'code_sha256':audit['code_sha256'],
                        'method':summary['method'],'windows':summary['windows'],
                        'confirmed':summary['confirmed_windows'],
                        'false_count_windows':summary['ever_false_count_windows'],
                        'safe_unseen_after_exact':summary['coordinator_audit']['safe_unseen_identity_after_exact_final'],
                        'sequence_holes':summary['coordinator_audit']['sequence_holes_seen'],
                        'cross_source_duplicates':summary['coordinator_audit']['cross_source_duplicates']})
    report={'runs':results,'all_complete':all(r['status']=='checked' for r in results),
            'original_failed_artifacts':original_artifact_check(ROOT)}
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--save',required=True)
    parser.add_argument('--overrides')
    args=parser.parse_args()
    result=check(ROOT/args.output,ROOT/args.overrides if args.overrides else None)
    target=ROOT/args.save
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'runs_checked':sum(r['status']=='checked' for r in result['runs']),
                      'incomplete':sum(r['status']!='checked' for r in result['runs']),
                      'all_complete':result['all_complete'],
                      'original_failed_artifacts':result['original_failed_artifacts']},ensure_ascii=False))
