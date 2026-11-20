"""Read completed runs, retain censoring/failures, and aggregate by UTC date."""
import argparse
import csv
import json
from pathlib import Path
import sys
import pandas as pd
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from ais_confirm.audit import sha256, original_artifact_check

def summarize(output,destination,allow_partial=False,overrides=None):
    with (ROOT/'run_matrix.csv').open(encoding='utf-8-sig',newline='') as h:
        expected={r['run_id']:r for r in csv.DictReader(h)}
    results,days,statuses=[],[],[]
    resolved_paths={}
    replaced=[]
    for run_id,spec in expected.items():
        path=output/run_id
        if overrides is not None and (overrides/run_id/'audit.json').exists():
            path=overrides/run_id
            replaced.append(run_id)
        resolved_paths[run_id]=path
        if not (path/'audit.json').exists():
            statuses.append({'run_id':run_id,'phase':spec['phase'],'status':'failed' if (path/'FAILED.txt').exists() else 'not_completed'})
            continue
        result=json.loads((path/'run_metrics.json').read_text(encoding='utf-8'))
        audit=json.loads((path/'audit.json').read_text(encoding='utf-8'))
        assert audit['config_sha256']==spec['config_sha256']
        statuses.append({'run_id':run_id,'phase':spec['phase'],'status':'completed'})
        flat={k:v for k,v in result.items() if not isinstance(v,dict)}
        flat.update({f'audit_{k}':v for k,v in result['coordinator_audit'].items()})
        results.append(flat)
        metrics=pd.read_csv(path/'window_metrics.csv')
        for column in ['network_bytes','control_bytes','identity_bytes','payload_bytes','retransmission_bytes']:
            if column not in metrics:
                metrics[column]=0
            else:
                metrics[column]=metrics[column].fillna(0)
        flat['ever_claimed_windows']=int(metrics.ever_claimed.sum())
        flat['capture_coverage_gap_total']=int(metrics.capture_coverage_gap.sum())
        metrics['date']=pd.to_datetime(metrics.window,unit='us',utc=True).dt.strftime('%Y-%m-%d')
        for date,group in metrics.groupby('date'):
            row={k:result[k] for k in ['run_id','phase','case_id','seed','copy_probability','delay_model','scheduler','method','delta_seconds']}
            row.update({'date':date,'windows':len(group),'restricted_mean_wait':group.restricted_wait.mean(),
                        'coverage':group.confirmed.mean(),'false_windows':group.ever_false_count.sum(),
                        'invalidated_claim_windows':group.ever_invalidated_claim.sum(),
                        'network_bytes':group.network_bytes.sum(),'control_bytes':group.control_bytes.sum(),
                        'identity_repair_count':group.identity_repair_count.sum(),
                        'numeric_repair_count':group.numeric_repair_count.sum(),
                        'raw_empty_windows':group.truth_trace.eq(0).sum(),
                        'capture_coverage_gap':group.capture_coverage_gap.sum()})
            row.update({f'mae_{h}':group[f'absolute_error_{h}'].mean() for h in [0,60,180,600,1200,3600,7200]})
            days.append(row)
    if not allow_partial:
        assert len(results)==len(expected),(len(results),len(expected))
    destination.mkdir(parents=True,exist_ok=False)
    runs=pd.DataFrame(results)
    daily=pd.DataFrame(days)
    runs['delta_seconds']=pd.to_numeric(runs.delta_seconds,errors='coerce')
    daily['delta_seconds']=pd.to_numeric(daily.delta_seconds,errors='coerce')
    pd.DataFrame(statuses).to_csv(destination/'execution_status.csv',index=False,encoding='utf-8-sig')
    runs.to_csv(destination/'run_summary.csv',index=False,encoding='utf-8-sig')
    daily.to_csv(destination/'daily_summary.csv',index=False,encoding='utf-8-sig')
    group_keys=['phase','case_id','copy_probability','delay_model','scheduler','method','delta_seconds']
    measures=['confirmation_coverage','restricted_mean_wait','ever_false_confirmation_rate','false_windows_all_rate',
              'network_bytes','control_bytes','identity_bytes','exact_final_claims','hash_final_claims',
              'proof_attempts','hash_budget_total','audit_hash_checks','audit_hash_failures',
              'audit_safe_unseen_identity_after_exact_final','audit_contract_violations',
              'mae_0','mae_60','mae_180','mae_600','mae_1200','mae_3600','mae_7200']
    runs.groupby(group_keys,dropna=False)[measures].mean().reset_index().to_csv(destination/'group_means.csv',index=False,encoding='utf-8-sig')
    # Pair date and seed first, then average seed replicates within each original date.
    replay=daily[daily.phase.eq('replay') & (daily.delta_seconds.isna() | daily.delta_seconds.eq(180))]
    keys=['date','seed','copy_probability','delay_model','scheduler']
    baseline=replay[replay.method.eq('UPSTREAM_COUNT_ONLY')][keys+['restricted_mean_wait','network_bytes','mae_180']]
    pairs=[]
    for method in ['PAYLOAD_BARRIER','FULL_ID_MANIFEST','ID_STREAM_30S','SEALED_DIFF_EXACT','SEALED_DIFF_HASH','WM_DELTA','TIMER_DELTA']:
        selected=replay[replay.method.eq(method)]
        matched=selected.merge(baseline,on=keys,suffixes=('','_baseline'),validate='one_to_one')
        for metric in ['restricted_mean_wait','network_bytes','mae_180']:
            matched[f'{metric}_difference']=matched[metric]-matched[f'{metric}_baseline']
        columns=[f'{m}_difference' for m in ['restricted_mean_wait','network_bytes','mae_180']]
        block=matched.groupby(['date','copy_probability','delay_model','scheduler'])[columns].mean().reset_index()
        block['method']=method
        pairs.append(block)
    pd.concat(pairs,ignore_index=True).to_csv(destination/'paired_date_differences_vs_count.csv',index=False,encoding='utf-8-sig')
    manifest={'status':'complete_matrix' if len(results)==len(expected) else 'partial_do_not_claim_complete',
              'completed':len(results),'expected':len(expected),
              'phases':pd.DataFrame(statuses).groupby(['phase','status']).size().rename('runs').reset_index().to_dict('records'),
              'original_failed_artifacts':original_artifact_check(ROOT),
              'code_versions':sorted({json.loads((resolved_paths[r['run_id']]/'audit.json').read_text())['code_sha256'] for r in results}),
              'replaced_run_ids':replaced,
              'resolved_run_paths':{r['run_id']:str(resolved_paths[r['run_id']]) for r in results},
              'notes':['run-level medians are not pooled population quantiles; use per-window records for those',
                       'H-censored windows stay in restricted mean waiting times',
                       'seeds do not create additional independent traffic days',
                       'last_status is the last protocol status; censored=True means UNCONFIRMED for evaluation',
                       'full_payload_completion_latency in original window_metrics is the observed SEAL+payload barrier time; it is not an oracle-only raw wire completion timestamp']}
    manifest['file_sha256']={p.name:sha256(p) for p in destination.iterdir() if p.is_file()}
    (destination/'summary_manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'completed':len(results),'expected':len(expected),'report':str(destination)},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--save',required=True)
    parser.add_argument('--allow-partial',action='store_true')
    parser.add_argument('--overrides')
    args=parser.parse_args()
    summarize(ROOT/args.output,ROOT/args.save,args.allow_partial,ROOT/args.overrides if args.overrides else None)
