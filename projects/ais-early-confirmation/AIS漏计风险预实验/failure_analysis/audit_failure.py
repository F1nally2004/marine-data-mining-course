"""Read-only audit of the FAILED, frozen pilot; all new output stays here.

No simulation, feature extraction, hyperparameter selection, clipping, or model
improvement is run. Original selected models are reconstructed at their saved C
only to inspect coefficients; their test predictions must match the saved ones.
"""
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import pilot


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc(seconds):
    return str(pd.Timestamp(seconds, unit='s', tz='UTC'))


def save(frame, name):
    frame.to_csv(HERE / name, index=False)


def main():
    protected = sorted(p for folder in [ROOT / 'data', ROOT / 'outputs']
                       for p in folder.rglob('*') if p.is_file())
    protected += sorted(p for p in ROOT.iterdir() if p.is_file())
    before = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    (HERE / 'original_hashes_before.json').write_text(
        json.dumps(before, indent=2, ensure_ascii=False), encoding='utf-8')

    events = pd.read_parquet(ROOT / 'data/events.parquet')
    times = np.sort(events.event_time.map(lambda x: int(x.timestamp())).unique())
    origin, end = times[0] // 86400 * 86400, times[-1] // 86400 * 86400
    starts = np.arange(origin, end, 600, dtype=np.int64)
    raw_counts = events.assign(window_start=events.event_time.map(
        lambda x: int(x.timestamp()) // 600 * 600)).groupby('window_start').size()
    raw_windows = pd.DataFrame({'window_start': starts})
    raw_windows['raw_records'] = raw_windows.window_start.map(raw_counts).fillna(0).astype(int)
    raw_windows['split'] = ((raw_windows.window_start - origin) // 86400).map(pilot.split_name)
    raw_windows['window_utc'] = raw_windows.window_start.map(utc)
    raw_windows['block'] = raw_windows.raw_records.eq(0).ne(
        raw_windows.raw_records.eq(0).shift()).cumsum()
    blocks = []
    for _, g in raw_windows[raw_windows.raw_records == 0].groupby('block'):
        blocks.append({'first_window_utc': utc(g.window_start.min()),
                       'last_window_utc': utc(g.window_start.max()),
                       'end_exclusive_utc': utc(g.window_start.max() + 600),
                       'windows': len(g), 'splits': ','.join(g.split.unique())})
    save(pd.DataFrame(blocks), 'raw_empty_blocks.csv')
    save(raw_windows.drop(columns='block'), 'raw_windows.csv')
    gap_index = np.argsort(np.diff(times))[-20:][::-1]
    save(pd.DataFrame([{'before_utc': utc(times[i]), 'after_utc': utc(times[i + 1]),
                        'gap_seconds': times[i + 1] - times[i]} for i in gap_index]), 'raw_gaps.csv')
    with sqlite3.connect((ROOT / 'data/example_data.db').as_uri() + '?mode=ro', uri=True) as con:
        lo, hi = times[gap_index[0]], times[gap_index[0] + 1]
        db_gap_count = con.execute('SELECT COUNT(*) FROM ais_202201_dynamic WHERE time>? AND time<?',
                                   (int(lo), int(hi))).fetchone()[0]
        db_rows = con.execute('SELECT COUNT(*) FROM ais_202201_dynamic').fetchone()[0]
    assert db_gap_count == 0

    metrics = pd.read_csv(ROOT / 'outputs/metrics.csv')
    saved = pd.read_csv(ROOT / 'outputs/test_predictions.csv')
    bounds, windows, split_scores, contributions, coefficients = [], [], [], [], []
    feature_ranges, correlations, candidate_coverage = [], [], []
    prediction_max_difference = 0.
    for scenario, seeds in [('sanity', [11]), ('iid', pilot.SEEDS), ('burst', pilot.SEEDS)]:
        for seed in seeds:
            stream = pd.read_parquet(ROOT / f'outputs/arrivals_{scenario}_{seed}.parquet')
            labels = pd.read_csv(ROOT / f'outputs/labels_{scenario}_{seed}.csv')
            merged_raw = labels.merge(raw_windows[['window_start', 'raw_records']], on='window_start')
            assert np.array_equal(merged_raw.truth_ships.eq(0), merged_raw.raw_records.eq(0))
            assert len(stream) == 39828 and stream.event_id.nunique() == len(stream)
            d = stream.arrival_s - stream.event_s
            bounds.append({'scenario': scenario, 'seed': seed, 'records': len(stream),
                           'max_message_delay_s': d.max(), 'min_message_delay_s': d.min(),
                           'test_raw_empty': int(merged_raw.query("split == 'test'").raw_records.eq(0).sum()),
                           'empty_agrees_with_raw': True})
            if scenario == 'sanity':
                continue
            f = pd.read_csv(ROOT / f'outputs/features_{scenario}_{seed}.csv')
            f['date_utc'] = f.window_start.map(utc).str[:10]
            train = f[f.split == 'train']
            lag_max = train.source_time_lag.max()
            test_saved = saved[(saved.scenario == scenario) & (saved.seed == seed)]
            joined = f[f.split == 'test'].merge(test_saved[
                ['window_start', 'M0', 'M1', 'M2']], on='window_start', validate='one_to_one')
            joined['scenario'], joined['seed'] = scenario, seed
            joined['date_utc'] = joined.window_start.map(utc).str[:10]
            joined['lag_above_train_max'] = joined.source_time_lag > lag_max
            joined['train_max_lag'] = lag_max
            joined['raw_empty'] = joined.truth_ships == 0  # oracle, post-hoc only
            for method in ['M0', 'M1', 'M2']:
                joined['loss_' + method] = (joined[method] - joined.y) ** 2
            joined['gain_M1'] = joined.loss_M0 - joined.loss_M1
            joined['gain_M2'] = joined.loss_M0 - joined.loss_M2
            windows.append(joined)
            if scenario == 'burst' and seed == 11:
                daily_feature_cols = ['truth_ships', 'seen_ships', 'history_mean_ships',
                                      'hist_absent_ships', 'hist_absent_frequency',
                                      'mature_delay_gt180', 'mature_delay_p90', 'source_time_lag']
                save(f.groupby('date_utc')[daily_feature_cols].mean().reset_index(),
                     'daily_features_burst11.csv')
            for h in pilot.HISTORY_FEATURES:
                correlations.append({'scenario': scenario, 'seed': seed, 'feature': h,
                                     'fraction_equal': np.isclose(joined[h], joined['sham_' + h]).mean(),
                                     'pearson': joined[h].corr(joined['sham_' + h])
                                     if joined[h].std() > 0 and joined['sham_' + h].std() > 0 else np.nan})

            feature_sets = [('M0', pilot.BASE_FEATURES),
                            ('M1', pilot.BASE_FEATURES + pilot.HISTORY_FEATURES),
                            ('M2', pilot.BASE_FEATURES + ['sham_' + h for h in pilot.HISTORY_FEATURES])]
            for method, cols in feature_sets:
                c = float(metrics.query('scenario == @scenario and seed == @seed and method == @method').C.iloc[0])
                model = make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=2000, tol=1e-8))
                model.fit(train[cols], train.y)
                scaler, lr = model.steps[0][1], model.steps[1][1]
                for j, feature in enumerate(cols):
                    coefficients.append({'scenario': scenario, 'seed': seed, 'method': method, 'C': c,
                                         'feature': feature, 'mean_train': scaler.mean_[j],
                                         'scale_train': scaler.scale_[j], 'beta_scaled': lr.coef_[0, j],
                                         'beta_raw': lr.coef_[0, j] / scaler.scale_[j],
                                         'intercept_scaled': lr.intercept_[0]})
                for split in ['train', 'val', 'test']:
                    part = f[f.split == split].copy()
                    part['p'] = model.predict_proba(part[cols])[:, 1]
                    if split == 'test':
                        check = part[['window_start', 'p']].merge(test_saved[['window_start', method]], on='window_start')
                        diff = float(np.abs(check.p - check[method]).max())
                        prediction_max_difference = max(prediction_max_difference, diff)
                        assert diff < 1e-10, (scenario, seed, method, diff)
                        z_contrib = scaler.transform(part[cols]) * lr.coef_[0]
                        assert np.allclose(z_contrib.sum(axis=1) + lr.intercept_[0], model.decision_function(part[cols]))
                        for name, mask in [('raw_empty', part.truth_ships.eq(0)),
                                           ('nonempty', part.truth_ships.gt(0)),
                                           ('postgap_20220118_nonempty', part.truth_ships.gt(0)
                                            & part.date_utc.eq('2022-01-18'))]:
                            for j, feature in enumerate(cols):
                                contributions.append({'scenario': scenario, 'seed': seed, 'method': method,
                                                      'stratum': name, 'feature': feature,
                                                      'mean_logit_contribution': z_contrib[mask, j].mean()})
                    for name, mask in [('all', np.ones(len(part), dtype=bool)),
                                       ('raw_empty', part.truth_ships.eq(0)),
                                       ('nonempty', part.truth_ships.gt(0)),
                                       ('seen_empty', part.seen_ships.eq(0))]:
                        g = part[mask]
                        if not len(g):
                            continue
                        split_scores.append({'scenario': scenario, 'seed': seed, 'method': method, 'split': split,
                                             'stratum': name, 'n': len(g), 'positive': int(g.y.sum()),
                                             'positive_rate': g.y.mean(), 'mean_probability': g.p.mean(),
                                             'brier': ((g.p - g.y) ** 2).mean(),
                                             'constant_train_prior_brier': ((train.y.mean() - g.y) ** 2).mean()})
            for feature in pilot.BASE_FEATURES + pilot.HISTORY_FEATURES:
                lo, hi = train[feature].min(), train[feature].max()
                for split in ['train', 'val', 'test']:
                    g = f[f.split == split]
                    feature_ranges.append({'scenario': scenario, 'seed': seed, 'feature': feature, 'split': split,
                                           'train_min': lo, 'train_max': hi, 'min': g[feature].min(),
                                           'max': g[feature].max(), 'outside_train_range': int((
                                               g[feature].lt(lo) | g[feature].gt(hi)).sum())})

            # How many actually missed ship-window pairs are even represented by the
            # causal historical candidate construction? This uses labels ONLY for audit.
            q = stream.assign(window_start=(stream.event_s // 600 * 600).astype('int64'))
            first = q.groupby(['window_start', 'mmsi'], as_index=False).arrival_s.min()
            first = first.merge(f[['window_start', 'split']], on='window_start')
            missed = first[(first.split == 'test') & (first.arrival_s > first.window_start + 780)]
            profiles_by_day = {}
            known, in_slot = 0, 0
            for row in missed.itertuples():
                cut = row.window_start // 86400 * 86400
                if cut not in profiles_by_day:
                    profiles_by_day[cut] = pilot.history_profiles(stream, cut)
                prof = profiles_by_day[cut].get(row.mmsi)
                known += prof is not None
                in_slot += prof is not None and prof[0][int(row.window_start // 600 % 144)] > 0
            candidate_coverage.append({'scenario': scenario, 'seed': seed, 'missed_ship_window_pairs': len(missed),
                                       'known_in_past_5days': known, 'eligible_same_slot_candidate': in_slot})

    w = pd.concat(windows, ignore_index=True)
    save(w, 'test_window_losses.csv')
    save(pd.DataFrame(bounds), 'simulation_bounds.csv')
    save(pd.DataFrame(split_scores), 'frozen_model_split_scores.csv')
    save(pd.DataFrame(coefficients), 'frozen_model_coefficients.csv')
    save(pd.DataFrame(contributions), 'frozen_model_logit_contributions.csv')
    save(pd.DataFrame(contributions).query(
        "scenario == 'burst' and seed == 11 and stratum == 'postgap_20220118_nonempty'"),
        'postgap_20220118_attribution.csv')
    save(pd.DataFrame(feature_ranges), 'feature_ranges.csv')
    save(pd.DataFrame(correlations), 'history_sham_similarity.csv')
    save(pd.DataFrame(candidate_coverage), 'historical_candidate_coverage.csv')

    grouped = []
    for scenario, g in w.groupby('scenario'):
        strata = {'all': np.ones(len(g), bool), 'raw_empty': g.raw_empty,
                  'nonempty': ~g.raw_empty, 'positive': g.y.eq(1),
                  'nonempty_negative': ~g.raw_empty & g.y.eq(0),
                  'lag_above_train_max': g.lag_above_train_max,
                  'lag_within_train_max': ~g.lag_above_train_max,
                  'nonempty_lag_above_train_max': ~g.raw_empty & g.lag_above_train_max,
                  'nonempty_lag_within_train_max': ~g.raw_empty & ~g.lag_above_train_max,
                  'seen_empty': g.seen_ships.eq(0),
                  'candidate_zero': g.hist_absent_ships.eq(0),
                  'candidate_nonzero': g.hist_absent_ships.gt(0)}
        for name, mask in strata.items():
            z = g[mask]
            if len(z):
                grouped.append({'scenario': scenario, 'stratum': name, 'n_across_seeds': len(z),
                                'positive_rate': z.y.mean(), **{f'brier_{m}': z['loss_' + m].mean()
                                                              for m in ['M0', 'M1', 'M2']},
                                'gain_M1': z.gain_M1.mean(), 'gain_M2': z.gain_M2.mean(),
                                **{f'mean_probability_{m}': z[m].mean() for m in ['M0', 'M1', 'M2']}})
    save(pd.DataFrame(grouped), 'test_stratified_scores.csv')
    daily = w.groupby(['scenario', 'seed', 'date_utc']).agg(
        n=('y', 'size'), positive=('y', 'sum'), raw_empty=('raw_empty', 'sum'),
        M0=('loss_M0', 'mean'), M1=('loss_M1', 'mean'), M2=('loss_M2', 'mean'),
        gain_M1=('gain_M1', 'mean'), gain_M2=('gain_M2', 'mean')).reset_index()
    save(daily, 'daily_scores_by_seed.csv')
    save(daily.drop(columns='seed').groupby(['scenario', 'date_utc']).mean(numeric_only=True).reset_index(),
         'daily_scores.csv')
    nonempty_daily = w[~w.raw_empty].groupby(['scenario', 'date_utc']).agg(
        n=('y', 'size'), M0=('loss_M0', 'mean'), M1=('loss_M1', 'mean'),
        M2=('loss_M2', 'mean'), gain_M1=('gain_M1', 'mean')).reset_index()
    save(nonempty_daily, 'nonempty_daily_scores.csv')
    save(w.groupby(['scenario', 'seed']).agg(gain_M1=('gain_M1', 'mean'),
                                            gain_M2=('gain_M2', 'mean')).reset_index(), 'seed_gains.csv')
    save(w[~w.raw_empty].groupby(['scenario', 'seed']).agg(
        gain_M1=('gain_M1', 'mean'), gain_M2=('gain_M2', 'mean')).reset_index(), 'nonempty_seed_gains.csv')

    # A compact picture of the frozen burst/11 test outputs, without smoothing.
    os.environ['MPLCONFIGDIR'] = str(HERE / '.matplotlib')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    z = w[(w.scenario == 'burst') & (w.seed == 11)].copy()
    x = pd.to_datetime(z.window_start, unit='s', utc=True)
    fig, axes = plt.subplots(3, 1, figsize=(12, 7.5), sharex=True, layout='constrained')
    axes[0].plot(x, z.truth_ships, label='Source-sample distinct count', color='#385f85')
    axes[0].plot(x, z.seen_ships, label='Observed at publication', color='#eb9036', alpha=.8)
    axes[0].set_ylabel('Ships'); axes[0].legend(loc='upper right')
    axes[1].plot(x, z.source_time_lag, color='#385f85')
    axes[1].axhline(z.train_max_lag.iloc[0], color='#a84943', linestyle='--', label='Training maximum')
    axes[1].set_yscale('log'); axes[1].set_ylabel('Source lag (s, log)'); axes[1].legend()
    for method, color in [('M0', '#385f85'), ('M1', '#a84943'), ('M2', '#66996d')]:
        axes[2].plot(x, z[method], label=method, color=color, alpha=.8, linewidth=1)
    pos = z.y.eq(1)
    axes[2].scatter(x[pos], np.full(pos.sum(), 1.04), marker='|', color='black', label='Actual miss')
    axes[2].set_ylim(-.02, 1.1); axes[2].set_ylabel('Predicted miss probability'); axes[2].legend(ncol=4)
    for ax in axes:
        ax.axvspan(pd.Timestamp('2022-01-17 18:00', tz='UTC'),
                   pd.Timestamp('2022-01-18 13:30', tz='UTC'), color='#e8c95f', alpha=.2)
        ax.grid(alpha=.15)
    axes[0].set_title('Frozen failed run: burst, seed 11; shaded = source-empty windows')
    axes[-1].set_xlabel('Window start (UTC)')
    fig.savefig(HERE / 'burst_11_failure.png', dpi=170)
    plt.close(fig)

    after = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    assert before == after, 'Original artifacts changed during the audit'
    summary = {'original_files_checked': len(before), 'all_original_files_unchanged': True,
               'original_database_rows': db_rows, 'database_rows_inside_largest_gap': db_gap_count,
               'maximum_prediction_reconstruction_difference': prediction_max_difference,
               'auditor_sha256': digest(Path(__file__)),
               'scope': 'Post-hoc explanation; no new out-of-sample performance claim; no parameter selection'}
    (HERE / 'audit_manifest.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2), flush=True)
    print(pd.DataFrame(grouped).to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
