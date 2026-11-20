import argparse
import csv
import gc
import json
import os
from pathlib import Path
import platform
import sys
import time
import traceback

from .schema import WINDOW, SECOND
from .ingest import load_events, synthetic_events, timestamp_us
from .multisource import schedule_captures
from .source import SourceState
from .coordinator import Coordinator, PUBLICATION_COLUMNS
from .engine import Engine
from .network import Network, LOG_COLUMNS
from .calibration import calibrate, hash_supplement
from .audit import sha256, code_hash, original_artifact_check
from .evaluator import evaluate

ROOT = Path(__file__).resolve().parents[2]
SOURCE_COLUMNS = ['source', 'window', 'epoch', 'seq', 'captured_at', 'event_time', 'mmsi', 'event_id']
CHECK_COLUMNS = ['request', 'source', 'window', 'epoch', 'ack_prefix', 'source_count', 'snapshot_size', 'pending_size', 'codec', 'body_bytes', 'time']

def memory_peak():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [(name, ctypes.c_size_t) for name in ['peak', 'working', 'peak_page', 'page', 'peak_nonpage', 'nonpage', 'pagefile', 'peak_pagefile']]
        mem = Memory()
        mem.cb = ctypes.sizeof(mem)
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = wintypes.HANDLE
        inspect = ctypes.windll.psapi.GetProcessMemoryInfo
        inspect.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        inspect.restype = wintypes.BOOL
        handle = current()
        if inspect(handle, ctypes.byref(mem), mem.cb):
            return mem.peak
    return None

def run_one(spec, cfg, calibration, output, input_records=None):
    import pandas as pd
    output.mkdir(parents=True, exist_ok=False)
    started, cpu_start = time.perf_counter(), time.process_time()
    start = timestamp_us(spec['slice_start_utc'])
    end = timestamp_us(spec['slice_end_utc_exclusive'])
    guard_end = end + cfg['semantics']['guard_windows_after']*WINDOW
    windows = list(range(start, end, WINDOW))
    protocol_windows = list(range(start, guard_end, WINDOW))
    if spec['phase'] == 'pressure':
        records = synthetic_events(spec, start, len(protocol_windows))
    else:
        records = input_records if input_records is not None else load_events(ROOT/cfg['input']['events'], start, guard_end)
    faults = json.loads(spec['case_parameters_json'])
    runtime_spec = {**spec, 'faults': faults}
    captures = schedule_captures(records, runtime_spec, cfg)
    engine = Engine(calibration['costs_us'])
    engine.now = start
    sources = []
    def delivered(msg, direction):
        if direction == 'down':
            engine.cpu(msg.source, {'serialize_byte': len(msg.payload)}, 1, sources[msg.source].handle_check, msg)
        else:
            coordinator.receive(msg, direction)
    network = Network(engine, cfg, runtime_spec, delivered)
    sources = [SourceState(i, cfg, spec['method'], engine, network.send, faults, int(spec['seed']))
               for i in range(cfg['sources']['count'])]
    coordinator = Coordinator(spec['method'], cfg, engine, network.send, protocol_windows,
                              int(spec['delta_seconds'] or 180),
                              [1 if faults.get('inject_old_epoch_control_messages') else 0]*cfg['sources']['count'])
    for when, i, record in captures:
        engine.at(when, 0, sources[i].on_capture, record)
    del captures
    for window in protocol_windows:
        engine.at(window+WINDOW, 3, coordinator.publish_initial, window)
        for src in sources:
            engine.at(window+WINDOW+30*SECOND, 2, src.on_close_timer, window)
            if spec['method'] == 'ID_STREAM_30S':
                for elapsed in range(30, 601, 30):
                    engine.at(window+elapsed*SECOND, 2, src.flush_batch, window)
        if spec['method'] == 'TIMER_DELTA':
            engine.at(window+WINDOW+int(spec['delta_seconds'] or 180)*SECOND, 3, coordinator.timer_final, window)
    until = end + cfg['semantics']['observation_horizon_seconds'] * SECOND
    engine.run(until)
    # Oracle creation is deliberately after the protocol event loop finishes.
    source_events = [row for src in sources for row in src.capture_log]
    metrics, summary, truth, trace = evaluate(coordinator, source_events, records, windows, cfg, network, until)
    summary.update({'run_id': spec['run_id'], 'phase': spec['phase'], 'method': spec['method'],
                    'case_id': spec['case_id'], 'seed': int(spec['seed']),
                    'copy_probability': float(spec['copy_probability']), 'scheduler': spec['scheduler'],
                    'delay_model': spec['delay_model'], 'delta_seconds': spec['delta_seconds'],
                    'engine_events': engine.events_executed, 'input_records_with_guard': len(records),
                    'captures_with_guard': len(source_events), 'cpu_model_us': engine.cpu_us,
                    'wall_seconds': time.perf_counter()-started, 'process_cpu_seconds': time.process_time()-cpu_start,
                    'process_lifetime_peak_memory_bytes': memory_peak(),
                    'coordinator_audit': coordinator.audit,
                    'source_reopens': sum(s.audit['reopens'] for s in sources),
                    'proof_attempts': len(coordinator.proof_log),
                    'hash_budget_total': sum(st.hash_budget for st in coordinator.states.values()),
                    'status': 'completed'})
    pd.DataFrame(source_events, columns=SOURCE_COLUMNS).to_parquet(output/'source_events.parquet', index=False)
    pd.DataFrame(network.log, columns=LOG_COLUMNS).to_parquet(output/'message_log.parquet', index=False)
    pd.DataFrame(coordinator.publications, columns=PUBLICATION_COLUMNS).to_parquet(output/'publications.parquet', index=False)
    pd.DataFrame([r for src in sources for r in src.check_log], columns=CHECK_COLUMNS).to_parquet(output/'source_checks.parquet', index=False)
    pd.DataFrame(coordinator.proof_log, columns=['window','source','request','time','passed','error_bound','candidate_n']).to_parquet(output/'proof_checks.parquet', index=False)
    pd.DataFrame(coordinator.events, columns=['window','time','source','cause']).to_parquet(output/'protocol_events.parquet', index=False)
    for name, table in [('captured', truth), ('trace', trace)]:
        pd.DataFrame([{'window': w, 'mmsis': sorted(table[w]), 'distinct_count': len(table[w])} for w in windows]).to_parquet(output/f'truth_{name}.parquet', index=False)
    pd.DataFrame(metrics).to_csv(output/'window_metrics.csv', index=False, encoding='utf-8-sig')
    (output/'run_metrics.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    audit = {'spec': spec, 'config_sha256': sha256(ROOT/'experiment_config.json'),
             'amendment_sha256': sha256(ROOT/'protocol_semantics_amendment.json'),
             'calibration_sha256': sha256(ROOT/'calibration.json'), 'code_sha256': code_hash(ROOT),
             'hash_calibration_sha256': sha256(ROOT/'hash_calibration.json') if (ROOT/'hash_calibration.json').exists() else None,
             'events_sha256': sha256(ROOT/cfg['input']['events']),
             'rng': 'blake2b keyed by seed + purpose namespace + stable event/message scope',
             'oracle_created_after_run': True, 'original_failed_artifacts': original_artifact_check(ROOT),
             'environment': {'python': sys.version, 'platform': platform.platform()},
             'output_sha256': {p.name: sha256(p) for p in sorted(output.iterdir()) if p.is_file()}}
    (output/'audit.json').write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding='utf-8')
    return summary

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['smoke','replay','pressure','faults','all'], default='smoke')
    parser.add_argument('--method')
    parser.add_argument('--exclude-hash', action='store_true')
    parser.add_argument('--output', default='outputs/exact_stage')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    cfg = json.loads((ROOT/'experiment_config.json').read_text(encoding='utf-8'))
    if sha256(ROOT/cfg['input']['database']) != cfg['input']['database_sha256']:
        raise AssertionError('database hash mismatch')
    original_artifact_check(ROOT)
    calibration = calibrate(ROOT/'calibration.json')
    supplement=hash_supplement(ROOT/'hash_calibration.json')
    calibration={**calibration,'costs_us':{**calibration['costs_us'],**supplement['costs_us']}}
    with (ROOT/'run_matrix.csv').open(encoding='utf-8-sig', newline='') as handle:
        specs = [r for r in csv.DictReader(handle) if (args.phase=='all' or r['phase']==args.phase)
                 and (not args.method or r['method']==args.method)
                 and (not args.exclude_hash or r['method']!='SEALED_DIFF_HASH')]
    if args.limit:
        specs = specs[:args.limit]
    out = ROOT/args.output
    out.mkdir(parents=True, exist_ok=True)
    (out/'selection.json').write_text(json.dumps({'selected_runs': len(specs), 'args': vars(args)}, indent=2), encoding='utf-8')
    completed = 0
    for index, spec in enumerate(specs, 1):
        destination = out/spec['run_id']
        if destination.exists():
            if args.resume and (destination/'run_metrics.json').exists() and (destination/'audit.json').exists():
                previous = json.loads((destination/'audit.json').read_text(encoding='utf-8'))
                if previous['code_sha256'] != code_hash(ROOT):
                    raise RuntimeError(f'cannot resume results from another code revision: {destination}')
                completed += 1
                continue
            raise FileExistsError(f'refusing to overwrite saved results: {destination}')
        try:
            result = run_one(spec, cfg, calibration, destination)
            completed += 1
            print(json.dumps({'index': index, 'of': len(specs), 'run_id': spec['run_id'],
                              **{k: result[k] for k in ['method','confirmed_windows','windows','ever_false_count_windows','wall_seconds']}}, ensure_ascii=False), flush=True)
        except Exception:
            (destination/'FAILED.txt').write_text(traceback.format_exc(), encoding='utf-8')
            raise
        gc.collect()
    print(json.dumps({'completed': completed, 'selected': len(specs), 'output': str(out)}, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
