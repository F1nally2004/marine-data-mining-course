"""Preserve an actual count-before-identity case and the eight smoke summaries."""
import argparse
import json
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--save',required=True)
    args=parser.parse_args()
    output=ROOT/args.output
    destination=ROOT/args.save
    destination.mkdir(parents=True,exist_ok=False)
    summaries=[]
    count_folder=None
    for path in output.glob('smoke_*/run_metrics.json'):
        row=json.loads(path.read_text(encoding='utf-8'))
        summaries.append({k:row[k] for k in ['run_id','method','windows','confirmed_windows','valid_latency_median','valid_latency_p95','restricted_mean_wait','ever_false_count_windows','network_bytes','control_bytes','identity_bytes','proof_attempts','hash_final_claims']})
        if row['method']=='UPSTREAM_COUNT_ONLY':
            count_folder=path.parent
    pd.DataFrame(summaries).sort_values('method').to_csv(destination/'smoke_summary.csv',index=False,encoding='utf-8-sig')
    src=pd.read_parquet(count_folder/'source_events.parquet')
    messages=pd.read_parquet(count_folder/'message_log.parquet')
    pubs=pd.read_parquet(count_folder/'publications.parquet')
    raw=messages[messages.kind.eq('DATA') & messages.rx.notna()].merge(src[['source','window','seq','mmsi']],left_on=['source','window','request'],right_on=['source','window','seq'],validate='one_to_one')
    cases=[]
    for final in pubs[pubs.status.eq('EXACT_FINAL')].itertuples():
        local=raw[raw.window.eq(final.window)].sort_values('rx')
        before=set(local.loc[local.rx.le(final.time),'mmsi'])
        if len(before)>=final.count:
            continue
        after=local[local.rx.gt(final.time) & ~local.mmsi.isin(before)]
        window_pubs=pubs[pubs.window.eq(final.window)]
        cases.append({'window':int(final.window),'confirmed_at':int(final.time),'confirmed_count':int(final.count),
                      'wire_delivered_distinct_at_confirmation':len(before),
                      'late_new_mmsis':sorted(map(int,set(after.mmsi))),
                      'any_reopen':bool(window_pubs.status.eq('REOPENED').any()),
                      'subsequent_published_counts':list(map(int,window_pubs.loc[window_pubs.time.ge(final.time),'count'])),
                      'note':'wire reception precedes coordinator CPU completion; this is an offline diagnostic, not protocol input'})
        local[['source','request','rx','mmsi']].to_csv(destination/f'case_{int(final.window)}_raw_arrivals.csv',index=False,encoding='utf-8-sig')
        window_pubs.to_csv(destination/f'case_{int(final.window)}_publications.csv',index=False,encoding='utf-8-sig')
    assert cases,'expected the observed numeric-anchor regression case in this frozen smoke'
    assert all(not c['any_reopen'] and set(c['subsequent_published_counts'])=={c['confirmed_count']} for c in cases)
    (destination/'confirmed_before_identity_cases.json').write_text(json.dumps(cases,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'smoke_runs':len(summaries),'actual_numeric_anchor_cases':cases},indent=2,ensure_ascii=False))

if __name__=='__main__':
    main()
