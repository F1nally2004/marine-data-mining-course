from .schema import SECOND, WINDOW
from .audit import uniform

def schedule_captures(events, spec, cfg):
    """Scheduler-only tuples. Never expose this list to a source/coordinator."""
    seed = int(spec['seed'])
    copy_p = float(spec['copy_probability'])
    faults = spec.get('faults', {})
    centers = cfg['sources']['centers_lon_lat']
    scale = cfg['sources']['distance_longitude_scale']
    captures = []
    for record in events:
        window = record.event_time // WINDOW * WINDOW
        if uniform(seed, 'blind-ship-window', record.mmsi, window) < faults.get('all_copies_drop_ship_window_probability', 0):
            continue
        ordered = sorted(range(len(centers)), key=lambda i: ((record.lon-centers[i][0])*scale)**2 + (record.lat-centers[i][1])**2)
        selected = [ordered[0]]
        if uniform(seed, 'copy', record.event_id) < copy_p:
            selected.append(ordered[1])
        for source in selected:
            lag = int(uniform(seed, 'capture-lag', source, record.event_id) * 30 * SECOND)
            if uniform(seed, 'capture-breach', source, record.event_id) < faults.get('capture_extra_delay_probability', 0):
                lag += faults['capture_extra_delay_seconds'] * SECOND
            captures.append((record.event_time + lag, source, record))
    return sorted(captures, key=lambda item: (item[0], item[1], item[2].event_id))
