"""Validate the frozen DESIGN and write its run matrix. Not a protocol simulator.

Only writes run_matrix.csv and design_manifest.json beside this script.
Original pilot inputs, outputs, and failed results are strictly read-only.
"""
import csv
import hashlib
import itertools
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def validate(cfg):
    assert cfg['status'] == 'frozen_design_not_experiment_results'
    assert cfg['semantics']['window_seconds'] == 600
    assert cfg['semantics']['first_provisional_lag_seconds'] == 0
    assert cfg['semantics']['late_updates_enabled_for_every_method']
    assert cfg['input']['retain_all_source_empty_windows']
    assert cfg['sources']['count'] == len(cfg['sources']['centers_lon_lat'])
    assert max(cfg['sources']['capture_lag_uniform_seconds']) <= cfg['sources']['trusted_capture_lateness_bound_seconds']
    assert cfg['sources']['empty_sources_must_emit_seal']
    for key in ['control_and_data_share_capacity', 'control_and_data_share_outages', 'same_delay_law_for_all_packet_types']:
        assert cfg['network'][key], key
    assert cfg['network']['rate_bytes_per_second_per_source'] > 0
    assert 0 < cfg['network']['control_wfq_capacity_share'] < 1
    assert cfg['network']['application_data_body_bytes'] >= 28
    assert cfg['network']['common_application_header_bytes'] == 48
    assert cfg['network']['max_application_frame_bytes'] > 48
    assert int(cfg['proof']['fingerprint_prime_decimal']) == (1 << 127) - 1
    assert cfg['proof']['fingerprint_point_uniform_and_independent_of_bloom_and_sets']
    assert cfg['proof']['bloom_all_positive_alone_never_confirms']
    assert cfg['proof']['one_hash_candidate_check_per_source_window_epoch_round']
    assert cfg['proof']['window_error_budget'] > 0
    assert cfg['proof']['shared_exact_cardinality_bound_fast_path']
    assert cfg['proof']['reported_count_separate_from_known_identity_set_size']
    assert all(cfg['leakage_guards'].values())
    names = [m['id'] for m in cfg['methods']]
    assert len(names) == len(set(names)) == 8
    assert set(names).isdisjoint(cfg['reference_only'])
    for method in cfg['methods']:
        if 'delta_seconds' in method:
            assert method['primary_delta_seconds'] in method['delta_seconds']
    for phase in cfg['phases'].values():
        assert all(0 <= p <= 1 for p in phase['copy_probabilities'])
        assert len(phase['seeds']) == len(set(phase['seeds']))


def method_variants(cfg, sweep):
    for method in cfg['methods']:
        if 'delta_seconds' in method:
            deltas = method['delta_seconds'] if sweep else [method['primary_delta_seconds']]
            for delta in deltas:
                yield method['id'], delta
        else:
            yield method['id'], ''


def make_rows(cfg, cfg_hash):
    rows = []
    for phase_name, phase in cfg['phases'].items():
        cases = phase.get('cases', [{'id': 'base'}])
        blocks = itertools.product(phase['seeds'], phase['copy_probabilities'],
                                   phase['delay_models'], phase['schedulers'], cases)
        for seed, copy_probability, delay, scheduler, case in blocks:
            for method, delta in method_variants(cfg, phase['use_delta_sweep']):
                slice_start = phase.get('slice_start_utc', cfg['input']['evaluation_start_utc'])
                slice_end = phase.get('slice_end_utc_exclusive', cfg['input']['evaluation_end_utc_exclusive'])
                if phase_name == 'pressure':
                    start = datetime.fromisoformat(slice_start.replace('Z', '+00:00'))
                    slice_end = (start + timedelta(seconds=phase['windows_per_case'] * cfg['semantics']['window_seconds'])).isoformat().replace('+00:00', 'Z')
                row = {
                    'phase': phase_name, 'case_id': case['id'], 'seed': seed,
                    'copy_probability': copy_probability, 'delay_model': delay,
                    'scheduler': scheduler, 'method': method, 'delta_seconds': delta,
                    'source_count': cfg['sources']['count'],
                    'capture_bound_seconds': cfg['sources']['trusted_capture_lateness_bound_seconds'],
                    'rate_bytes_per_second': cfg['network']['rate_bytes_per_second_per_source'],
                    'data_body_bytes': cfg['network']['application_data_body_bytes'],
                    'distinct_ships': case.get('distinct_ships', ''),
                    'messages_per_ship_per_window': case.get('messages_per_ship_per_window', ''),
                    'synthetic_windows': phase.get('windows_per_case', ''),
                    'guard_windows_after': cfg['semantics']['guard_windows_after'],
                    'slice_start_utc': slice_start,
                    'slice_end_utc_exclusive': slice_end,
                    'case_parameters_json': canonical(case), 'config_sha256': cfg_hash,
                }
                row['run_id'] = phase_name + '_' + hashlib.sha256(canonical(row).encode('utf-8')).hexdigest()[:16]
                rows.append({'run_id': row.pop('run_id'), **row})
    assert len({r['run_id'] for r in rows}) == len(rows)
    return rows


def verify_old_artifacts():
    old_root = ROOT.parent / 'AIS漏计风险预实验'
    prior = old_root / 'failure_analysis/original_hashes_before.json'
    if not prior.exists():
        return {'prior_failure_hash_manifest_available': False}
    expected = json.loads(prior.read_text(encoding='utf-8'))
    changed = [rel for rel, digest in expected.items() if sha256(old_root / rel) != digest]
    assert not changed, f'Original failed artifacts changed: {changed}'
    return {'prior_failure_hash_manifest_available': True,
            'original_files_checked': len(expected), 'all_unchanged': True}


def verify_math_examples():
    """Independent finite examples, not tests of an unimplemented protocol."""
    universe = set(range(1, 7))
    cases = [({1}, 3, {1}, 1), (set(), 2, set(), 2), ({1}, 3, {2}, 2)]
    bounds = []
    for k1, n1, k2, n2 in cases:
        possible1 = [set(x) for x in itertools.combinations(universe, n1) if k1.issubset(x)]
        possible2 = [set(x) for x in itertools.combinations(universe, n2) if k2.issubset(x)]
        truth_sizes = [len(u1 | u2) for u1 in possible1 for u2 in possible2]
        known = k1 | k2
        lower = max(len(known), n1, n2)
        upper = len(known) + n1 - len(k1) + n2 - len(k2)
        assert (min(truth_sizes), max(truth_sizes)) == (lower, upper)
        bounds.append({'known': sorted(known), 'source_counts': [n1, n2],
                       'lower': lower, 'upper': upper, 'exhaustive_match': True})

    def fp(keys, point, prime):
        product = 1
        for key in set(keys):
            assert 0 <= key < prime
            product = product * (point - key) % prime
        return product

    prime = 257
    left, right = {1, 2}, {3, 4}
    collisions = [r for r in range(prime) if fp(left, r, prime) == fp(right, r, prime)]
    assert len(collisions) == 1 and len(collisions) <= len(left) - 1
    assert fp([1, 1, 2], 17, prime) == fp([2, 1], 17, prime)
    return {'scope': 'finite mathematical design checks only; no simulator run',
            'cardinality_bound_examples': bounds,
            'small_field_prime': prime, 'distinct_sets': [sorted(left), sorted(right)],
            'collision_points': collisions, 'degree_bound_verified': True,
            'set_order_and_duplicate_invariance': True}


def main():
    config_path = ROOT / 'experiment_config.json'
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    validate(cfg)
    db = (ROOT / cfg['input']['database']).resolve()
    events = (ROOT / cfg['input']['events']).resolve()
    assert events.is_file(), events
    assert sha256(db) == cfg['input']['database_sha256']
    old_check = verify_old_artifacts()
    math_check = verify_math_examples()
    cfg_hash = sha256(config_path)
    rows = make_rows(cfg, cfg_hash)
    counts = dict(Counter(r['phase'] for r in rows))
    assert counts == {'smoke': 8, 'replay': 560, 'pressure': 160, 'faults': 120}, counts
    matrix_path = ROOT / 'run_matrix.csv'
    with matrix_path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    files = [config_path, Path(__file__), ROOT / '技术方案与实验规范.md', ROOT / '代码模块与实施顺序.md', matrix_path]
    manifest = {
        'status': 'design_preparation_only_no_protocol_experiments_run',
        'run_count': len(rows), 'counts_by_phase': counts,
        'local_file_sha256': {p.name: sha256(p) for p in files},
        'input_events_sha256': sha256(events), 'input_database_sha256': sha256(db),
        'original_failed_artifacts': old_check,
        'mathematical_design_checks': math_check,
        'cpu_calibration_exists': (ROOT / cfg['compute']['calibration_file']).is_file(),
        'implementation_status': 'protocol_modules_not_implemented_yet',
        'note': 'Use this frozen matrix for implementation; preparation is not performance evidence.'
    }
    (ROOT / 'design_manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'run_count': len(rows), 'phases': counts,
                      'original_failed_artifacts': old_check,
                      'protocol_experiments_run': 0}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
