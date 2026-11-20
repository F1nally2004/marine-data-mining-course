"""Offline oracle only. No evaluator references ever enter protocol constructors."""
from collections import defaultdict
import math
import statistics
from .schema import SECOND, WINDOW, FINAL

def evaluate(coordinator, source_events, records, windows, cfg, network, until):
    truth, trace, source_sets = defaultdict(set), defaultdict(set), defaultdict(lambda: defaultdict(set))
    for source, w, epoch, seq, captured, event_time, mmsi, event_id in source_events:
        truth[w].add(mmsi)
        source_sets[w][source].add(mmsi)
    for record in records:
        trace[record.event_time // WINDOW * WINDOW].add(record.mmsi)
    by_window = defaultdict(list)
    for row in coordinator.publications:
        by_window[row[0]].append(row)
    bytes_by_window = defaultdict(lambda: defaultdict(int))
    for row in network.log:
        source, w = row[2], row[3]
        # Attribute traffic to its target window and cap it at that window's H.
        deadline = min(until, w + WINDOW + cfg['semantics']['observation_horizon_seconds'] * SECOND)
        sent = network.sent_bytes_by(row, deadline)
        stats = bytes_by_window[w]
        stats['offered_network_bytes'] += row[14]
        stats['network_bytes'] += sent
        stats['application_bytes'] += min(row[13], sent)
        stats['payload_bytes' if row[1] == 'DATA' else 'control_bytes'] += sent
        stats['identity_bytes'] += min(row[15], sent)
        stats['retransmission_bytes'] += sent if row[16] else 0
        stats['up_bytes' if row[8] == 'up' else 'down_bytes'] += sent
    metrics = []
    horizon = cfg['semantics']['observation_horizon_seconds']
    for w in windows:
        n = len(truth[w])
        b = w + WINDOW
        cutoff = b + horizon * SECOND
        full_history = by_window[w]
        pubs = [p for p in full_history if p[1] <= cutoff]
        claims = [p for p in pubs if p[4] in FINAL]
        last_reopen = max((p[2] for p in full_history if p[4] == 'REOPENED'), default=-1)
        valid = [p for p in claims if p[3] == n and p[2] > last_reopen]
        false = [p for p in claims if p[3] != n]
        invalidated = [p for p in claims if p[2] < last_reopen]
        st = coordinator.states[w]
        latency = (valid[0][1]-b)/SECOND if valid else None
        row = {'window': w, 'truth_captured': n, 'truth_trace': len(trace[w]),
               'capture_coverage_gap': len(trace[w] - truth[w]),
               'first_publication_latency': (pubs[0][1]-b)/SECOND if pubs else None,
               'first_claim_latency': (claims[0][1]-b)/SECOND if claims else None,
               'valid_confirmation_latency': latency,
               'restricted_wait': latency if latency is not None else horizon,
               'confirmed': bool(valid), 'ever_claimed': bool(claims),
               'ever_false_count': bool(false), 'ever_invalidated_claim': bool(invalidated),
               'claim_count': len(claims), 'false_claim_count': len(false),
               'censored': not bool(valid), 'last_status': pubs[-1][4] if pubs else 'UNCONFIRMED',
               'last_count': pubs[-1][3] if pubs else 0,
               'identity_repair_count': st.identity_repairs, 'numeric_repair_count': st.numeric_repairs,
               'repair_class': 'identity_repair' if st.identity_repairs else ('numeric_repair' if st.numeric_repairs else 'no_repair'),
               'source_closure_wait': (st.seal_ready_at-b)/SECOND if st.seal_ready_at is not None else None,
               'verification_wait': (valid[0][1]-st.seal_ready_at)/SECOND if valid and st.seal_ready_at is not None else None,
               'full_payload_completion_latency': (st.full_payload_at-b)/SECOND if st.full_payload_at is not None else None,
               'hash_error_budget': st.hash_budget,
               'exact_final_claims': sum(p[4]=='EXACT_FINAL' for p in claims),
               'hash_final_claims': sum(p[4]=='HASH_FINAL' for p in claims),
               'set_overlap_fraction': (sum(map(len, source_sets[w].values()))-n) / max(1, sum(map(len, source_sets[w].values()))),
               **bytes_by_window[w]}
        for h in cfg['evaluation']['common_horizons_seconds']:
            before = [p for p in pubs if p[1] <= b+h*SECOND]
            value = before[-1][3] if before else 0
            row[f'absolute_error_{h}'] = abs(value-n)
            row[f'relative_error_{h}'] = abs(value-n)/max(1,n)
            row[f'missed_{h}'] = max(0,n-value)
        metrics.append(row)
    count = len(metrics)
    valid_latencies = sorted(m['valid_confirmation_latency'] for m in metrics if m['confirmed'])
    def quantile(q):
        # Censor at a common administrative H. Do not compute success-only quantiles.
        rank = math.ceil(q*count)
        return valid_latencies[rank-1] if rank and len(valid_latencies)>=rank else None
    claim_windows = sum(m['ever_claimed'] for m in metrics)
    false_windows = sum(m['ever_false_count'] for m in metrics)
    summary = {'windows': count, 'confirmed_windows': len(valid_latencies),
               'confirmation_coverage': len(valid_latencies)/max(1,count),
               'valid_latency_median': quantile(.5), 'valid_latency_p95': quantile(.95),
               'restricted_mean_wait': statistics.mean(m['restricted_wait'] for m in metrics),
               'ever_false_confirmation_rate': false_windows/claim_windows if claim_windows else None,
               'false_windows_all_rate': false_windows/max(1,count),
               'ever_false_count_windows': false_windows,
               'invalidated_claim_windows': sum(m['ever_invalidated_claim'] for m in metrics),
               'network_bytes': sum(m.get('network_bytes',0) for m in metrics),
               'control_bytes': sum(m.get('control_bytes',0) for m in metrics),
               'identity_bytes': sum(m.get('identity_bytes',0) for m in metrics),
               'exact_final_claims': sum(m['exact_final_claims'] for m in metrics),
               'hash_final_claims': sum(m['hash_final_claims'] for m in metrics),
               'empty_truth_windows': sum(m['truth_captured']==0 for m in metrics)}
    for h in cfg['evaluation']['common_horizons_seconds']:
        summary[f'mae_{h}'] = statistics.mean(m[f'absolute_error_{h}'] for m in metrics)
    return metrics, summary, truth, trace
