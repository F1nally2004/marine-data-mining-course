from datetime import datetime
from .schema import Record, SECOND, WINDOW

def timestamp_us(value):
    return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * SECOND)

def load_events(path, start, end):
    import pandas as pd
    data = pd.read_parquet(path)
    times = data.event_time.astype('int64').to_numpy()
    # The frozen input is datetime64[us, UTC]; make unit handling explicit.
    unit = data.event_time.dtype.unit
    if unit == 'ns':
        times = times // 1000
    elif unit != 'us':
        raise ValueError(f'unsupported timestamp unit {unit}')
    selected = (times >= start) & (times < end)
    values = data.loc[selected, ['event_id', 'mmsi', 'lon', 'lat']].itertuples(index=False, name=None)
    records = [Record(int(i), int(m), int(t), float(lon), float(lat))
               for (i, m, lon, lat), t in zip(values, times[selected])]
    if any(not 0 < r.mmsi <= 0xffffffff for r in records):
        raise ValueError('invalid MMSI; no silent filtering')
    return records

def synthetic_events(spec, start, windows):
    from .audit import uniform
    n, repeats, seed = int(spec['distinct_ships']), int(spec['messages_per_ship_per_window']), int(spec['seed'])
    records = []
    event_id = 0
    for w in range(windows):
        for ship in range(n):
            for report in range(repeats):
                t = start + w * WINDOW + int(uniform(seed, 'synthetic-time', w, ship, report) * WINDOW)
                lon = -71 + 5 * uniform(seed, 'synthetic-lon', w, ship, report)
                lat = 40.46 + 4.7 * uniform(seed, 'synthetic-lat', w, ship, report)
                records.append(Record(event_id, 200000000 + ship, t, lon, lat))
                event_id += 1
    return records
