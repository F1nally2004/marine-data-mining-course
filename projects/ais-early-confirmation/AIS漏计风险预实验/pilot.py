"""Causal-prefix AIS replay pilot. No adaptive watermark or waiting policy is fitted."""
import argparse
import hashlib
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score, average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent
WINDOW, PUBLISH_LAG, HORIZON, HISTORY_DAYS = 600, 180, 3600, 5
SEEDS = [11, 22, 33, 44, 55]
BASE_FEATURES = ['log_messages', 'log_ships', 'singleton_fraction', 'log_arrivals_60s',
                 'source_time_lag', 'hour_sin', 'hour_cos', 'weekday',
                 'history_mean_ships', 'mature_delay_gt180', 'mature_delay_p90', 'mature_delay_p99']
HISTORY_FEATURES = ['hist_absent_ships', 'hist_absent_frequency', 'hist_gap_log_median',
                    'hist_gap_unknown_fraction', 'hist_no_gap']


def stable_group(mmsi):
    return int.from_bytes(hashlib.sha256(str(int(mmsi)).encode()).digest()[:4], 'big') % 4


def simulate(events, scenario, seed):
    """Simulator sees truth; its schedule/group metadata is NEVER a predictor feature."""
    d = events.copy()
    e = d.event_s.to_numpy(dtype=float)
    rng = np.random.Generator(np.random.PCG64(seed))
    queued = np.zeros(len(d), dtype=bool)
    if scenario == 'iid':
        slow = rng.random(len(d)) < 0.1
        delay = np.where(slow, rng.uniform(240, 1200, len(d)), rng.uniform(0, 20, len(d)))
        arrival = e+delay
    else:
        arrival = e+rng.uniform(0, 20, len(d))
        if scenario == 'burst':
            original = arrival.copy()
            groups = np.array([stable_group(x) for x in d.mmsi])
            # Separate RNG stream: event count/order does not alter the outage schedule.
            schedule = np.random.Generator(np.random.PCG64(seed+100000))
            for group in range(4):
                for hour in range(int(original.min()//3600), int(original.max()//3600)+1):
                    if schedule.random() < 0.2:
                        start = hour*3600+schedule.uniform(0,2400)
                        stop = start+1200
                        mask = (groups == group) & (original >= start) & (original < stop)
                        arrival[mask] = stop+schedule.uniform(0,10,int(mask.sum()))
                        queued[mask] = True
        elif scenario != 'sanity':
            raise ValueError(scenario)
    d['arrival_s'] = np.round(arrival, 3)
    d['queued'] = queued
    d = d.sort_values(['arrival_s','event_id']).reset_index(drop=True)
    prior_max = np.maximum.accumulate(d.event_s.to_numpy())
    prior_max = np.r_[-np.inf, prior_max[:-1]]
    d['out_of_order'] = d.event_s.to_numpy() < prior_max
    # Diagnostic event-lateness only. It does not cause drops in this fixed-cutoff study.
    d['behind_watermark'] = d.event_s.to_numpy() < prior_max-PUBLISH_LAG
    d['after_publish'] = d.arrival_s > ((d.event_s//WINDOW+1)*WINDOW+PUBLISH_LAG)
    assert (d.arrival_s >= d.event_s).all()
    assert (d.arrival_s-d.event_s < HORIZON).all()
    return d


def make_labels(stream, start, end):
    """Every window, including empty ones. A ship is missed iff its first arrival is late."""
    d = stream.copy()
    d['window_start'] = (d.event_s//WINDOW)*WINDOW
    first = d.groupby(['window_start','mmsi'], as_index=False).arrival_s.min()
    first['seen_T'] = first.arrival_s <= first.window_start+WINDOW+PUBLISH_LAG
    first['seen_H'] = first.arrival_s <= first.window_start+WINDOW+HORIZON
    labels = pd.DataFrame({'window_start': np.arange(start,end,WINDOW,dtype=np.int64)})
    sums = first.groupby('window_start').agg(truth_ships=('seen_H','sum'), seen_ships=('seen_T','sum'))
    labels = labels.join(sums, on='window_start').fillna(0)
    labels[['truth_ships','seen_ships']] = labels[['truth_ships','seen_ships']].astype(int)
    labels['missed_ships'] = labels.truth_ships-labels.seen_ships
    labels['y'] = (labels.missed_ships > 0).astype(int)
    assert (labels.missed_ships >= 0).all()
    assert first.seen_H.all(), 'Horizon does not contain the simulated truth'
    return labels


def history_profiles(stream, cut):
    """Freeze at UTC midnight; never read a record unavailable at that cut."""
    h = stream.loc[(stream.event_s >= cut-HISTORY_DAYS*86400) &
                   (stream.event_s < cut) & (stream.arrival_s <= cut)]
    profiles = {}
    for ship, rows in h.groupby('mmsi', sort=True):
        times = np.sort(rows.event_s.to_numpy())
        diffs = np.diff(times)
        diffs = diffs[diffs > 0]
        # One contribution per ship/day/time-slot; repetitions do not inflate appearance frequency.
        unique_windows = np.unique(times//WINDOW)
        freq = np.bincount((unique_windows % 144).astype(int), minlength=144)/HISTORY_DAYS
        gap = float(np.median(diffs)) if len(diffs) else np.nan
        profiles[int(ship)] = (freq,gap)
    return profiles


def entity_features(profiles, seen, slot):
    candidates = [(freq[slot], gap) for ship,(freq,gap) in profiles.items()
                  if ship not in seen and freq[slot] > 0]
    gaps = [np.log1p(g) for _,g in candidates if np.isfinite(g)]
    n = len(candidates)
    return [n, sum(f for f,_ in candidates), float(np.median(gaps)) if gaps else 0.,
            (n-len(gaps))/n if n else 0., float(not gaps)]


def make_features(stream, starts, origin, seed):
    """Only arrival-prefix masks and frozen historical profiles enter this function's outputs."""
    e = stream.event_s.to_numpy()
    a = stream.arrival_s.to_numpy()
    ids = stream.mmsi.to_numpy()
    cache, rows = {}, []
    for s in starts:
        s = int(s); t = s+WINDOW+PUBLISH_LAG
        day_cut = (s//86400)*86400
        if day_cut not in cache:
            p = history_profiles(stream,day_cut)
            keys = sorted(p)
            rng = np.random.Generator(np.random.PCG64(seed+int(day_cut//86400)+200000))
            perm = rng.permutation(len(keys))
            sham = {key:p[keys[j]] for key,j in zip(keys,perm)}
            cache[day_cut] = p,sham
        profiles,sham = cache[day_cut]
        mask = (e >= s) & (e < s+WINDOW) & (a <= t)
        observed = ids[mask]
        seen, counts = np.unique(observed, return_counts=True)
        seen_set = set(seen)
        upto = np.searchsorted(a,t,side='right')
        last60 = upto-np.searchsorted(a,t-60,side='right')
        source_lag = max(0., t-float(e[:upto].max())) if upto else HORIZON
        mature = (e >= t-HORIZON-86400) & (e <= t-HORIZON) & (a <= t)
        delays = a[mature]-e[mature]
        slot = (s//WINDOW) % 144
        phase = (s%86400)/86400*2*np.pi
        base = [np.log1p(len(observed)),np.log1p(len(seen)),
                float((counts == 1).mean()) if len(counts) else 0.,np.log1p(last60),source_lag,
                np.sin(phase),np.cos(phase),int(pd.Timestamp(s,unit='s',tz='UTC').day_of_week),
                sum(freq[slot] for freq,_ in profiles.values()),
                float((delays > PUBLISH_LAG).mean()) if len(delays) else 0.,
                float(np.quantile(delays,.9)) if len(delays) else 0.,
                float(np.quantile(delays,.99)) if len(delays) else 0.]
        row = {'window_start':s,'day_index':int((s-origin)//86400)}
        row.update(zip(BASE_FEATURES,base))
        row.update(zip(HISTORY_FEATURES,entity_features(profiles,seen_set,slot)))
        row.update(zip(['sham_'+x for x in HISTORY_FEATURES],entity_features(sham,seen_set,slot)))
        rows.append(row)
    return pd.DataFrame(rows)


def split_name(day):
    # 19 complete days: 5 warmup, 6 train, 1 gap, 2 val, 1 gap, 4 test.
    if day < 5: return 'warmup'
    if day < 11: return 'train'
    if day == 11: return 'gap'
    if day < 14: return 'val'
    if day == 14: return 'gap'
    return 'test'


def compare(frame, scenario, seed):
    assert not set(BASE_FEATURES+HISTORY_FEATURES) & {'y','truth_ships','missed_ships','queued'}
    train = frame[frame.split == 'train']
    val = frame[frame.split == 'val']
    test = frame[frame.split == 'test']
    # Train labels must be mature before validation; validation labels before test.
    assert train.window_start.max()+WINDOW+HORIZON <= val.window_start.min()+WINDOW+PUBLISH_LAG
    assert val.window_start.max()+WINDOW+HORIZON <= test.window_start.min()+WINDOW+PUBLISH_LAG
    metrics, predictions = [], test[['window_start','day_index','y','missed_ships']].copy()
    for name, cols in [('M0',BASE_FEATURES),('M1',BASE_FEATURES+HISTORY_FEATURES),
                       ('M2',BASE_FEATURES+['sham_'+x for x in HISTORY_FEATURES])]:
        selected_c = None
        if train.y.nunique() < 2:
            prob = np.full(len(test),float(train.y.mean()))
            status = 'constant: single-class training labels'
        else:
            best = (np.inf,None,None)
            for c in [.01,.1,1.,10.]:
                model = make_pipeline(StandardScaler(), LogisticRegression(C=c,max_iter=2000,tol=1e-8))
                model.fit(train[cols], train.y)
                loss = brier_score_loss(val.y, model.predict_proba(val[cols])[:,1])
                if loss < best[0]: best = (loss,c,model)
            _,selected_c,model = best
            prob = model.predict_proba(test[cols])[:,1]
            status = 'ok'
        predictions[name] = prob
        metrics.append({'scenario':scenario,'seed':seed,'method':name,'C':selected_c,
                        'brier':float(brier_score_loss(test.y,prob)),
                        'auc':float(roc_auc_score(test.y,prob)) if test.y.nunique()>1 else None,
                        'average_precision':float(average_precision_score(test.y,prob)) if test.y.sum() else None,
                        'train_positive':int(train.y.sum()),'val_positive':int(val.y.sum()),
                        'test_positive':int(test.y.sum()),'test_windows':len(test),'status':status})
    predictions['brier_gain_M1'] = (predictions.y-predictions.M0)**2-(predictions.y-predictions.M1)**2
    predictions['brier_gain_M2'] = (predictions.y-predictions.M0)**2-(predictions.y-predictions.M2)**2
    predictions['scenario'],predictions['seed'] = scenario,seed
    return metrics,predictions


def raw_set_check(stream, labels):
    """Independent, literal set implementation audits optimized groupby labels."""
    e,a,m = stream.event_s.to_numpy(),stream.arrival_s.to_numpy(),stream.mmsi.to_numpy()
    for row in labels.itertuples():
        inside = (e >= row.window_start) & (e < row.window_start+WINDOW)
        seen = set(m[inside & (a <= row.window_start+WINDOW+PUBLISH_LAG)])
        truth = set(m[inside & (a <= row.window_start+WINDOW+HORIZON)])
        assert len(truth-seen) == row.missed_ships


def run():
    out = ROOT/'outputs'; out.mkdir(exist_ok=True)
    events = pd.read_parquet(ROOT/'data/events.parquet')
    # Exclude a source-truncated final date, not windows chosen on outcomes.
    origin = int(events.event_time.min().floor('D').timestamp())
    end = int(events.event_time.max().floor('D').timestamp())
    # Timestamp conversion independent of pandas timestamp storage unit.
    events['event_s'] = events.event_time.map(lambda x:int(x.timestamp())).astype('int64')
    events = events.loc[(events.event_s >= origin) & (events.event_s < end)].copy()
    assert (end-origin)//86400 == 19, 'This pinned pilot expects 19 complete calendar days'
    stats, metrics, pred_frames, audits = [],[],[],[]
    for scenario,seeds in [('sanity',[11]),('iid',SEEDS),('burst',SEEDS)]:
        for seed in seeds:
            print('running',scenario,seed,flush=True)
            stream = simulate(events,scenario,seed)
            labels = make_labels(stream,origin,end)
            raw_set_check(stream,labels)
            if scenario == 'sanity': assert labels.y.sum() == 0
            stream.to_parquet(out/f'arrivals_{scenario}_{seed}.parquet',index=False)
            label_out = labels.copy()
            label_out['day_index'] = (label_out.window_start-origin)//86400
            label_out['split'] = label_out.day_index.map(split_name)
            label_out.to_csv(out/f'labels_{scenario}_{seed}.csv',index=False)
            for split,g in label_out.groupby('split'):
                stats.append({'scenario':scenario,'seed':seed,'split':split,'windows':len(g),
                              'positive_windows':int(g.y.sum()),'positive_rate':float(g.y.mean()),
                              'missed_ship_windows':int(g.missed_ships.sum())})
            audits.append({'scenario':scenario,'seed':seed,'records':len(stream),
                           'out_of_order_rate':float(stream.out_of_order.mean()),
                           'behind_watermark_rate':float(stream.behind_watermark.mean()),
                           'after_publish_rate':float(stream.after_publish.mean()),
                           'queued_rate':float(stream.queued.mean()),'set_audit':'pass'})
            if scenario != 'sanity':
                features = make_features(stream,labels.window_start.to_numpy(),origin,seed)
                frame = features.merge(labels,on='window_start',validate='one_to_one')
                frame['split'] = frame.day_index.map(split_name)
                frame.to_csv(out/f'features_{scenario}_{seed}.csv',index=False)
                scores,pred = compare(frame,scenario,seed)
                metrics.extend(scores); pred_frames.append(pred)
    pd.DataFrame(stats).to_csv(out/'label_counts.csv',index=False)
    pd.DataFrame(audits).to_csv(out/'simulation_audit.csv',index=False)
    scores = pd.DataFrame(metrics); scores.to_csv(out/'metrics.csv',index=False)
    preds = pd.concat(pred_frames,ignore_index=True)
    preds.to_csv(out/'test_predictions.csv',index=False)
    daily = preds.groupby(['scenario','day_index'])[['brier_gain_M1','brier_gain_M2']].mean()
    daily.to_csv(out/'paired_by_day.csv')
    import sklearn,pyarrow
    meta = {'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,
            'sklearn':sklearn.__version__,'pyarrow':pyarrow.__version__,
            'source_rows_used':len(events),'ships':int(events.mmsi.nunique()),'days':19,
            'history_days':HISTORY_DAYS,'window_s':WINDOW,'publish_lag_s':PUBLISH_LAG,'horizon_s':HORIZON,
            'seeds':SEEDS,'rng':'PCG64','source_sha256':hashlib.sha256((ROOT/'data/example_data.db').read_bytes()).hexdigest(),
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'feature_freeze':'UTC midnight, arrival-prefix filtered',
            'limitations':['Tutorial-selected sample, not complete regional traffic',
                           'Only four test days; seeds are not independent traffic realizations',
                           'Delayed timestamps are simulated, not actual receiver times']}
    (out/'run_manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    print(scores.groupby(['scenario','method']).brier.agg(['mean','min','max']).to_string(),flush=True)
    print(pd.DataFrame(stats).query("split == 'test'").to_string(index=False),flush=True)


if __name__ == '__main__': run()
