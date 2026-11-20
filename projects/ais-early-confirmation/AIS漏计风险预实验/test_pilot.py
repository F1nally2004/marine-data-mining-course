"""Semantic tests: independent expected labels, boundaries, causal prefixes and replay invariants."""
import unittest

import numpy as np
import pandas as pd

import pilot


class SemanticTests(unittest.TestCase):
    def test_unique_late_vs_repeated_late(self):
        # Two accepted A messages; a late A does nothing, but a late B adds one ship.
        d = pd.DataFrame({'event_s':[10,20,30,40,600], 'arrival_s':[10,20,1000,1100,600],
                          'mmsi':[1,1,1,2,3]})
        labels = pilot.make_labels(d,0,1200)
        self.assertEqual(labels.seen_ships.tolist(),[1,1])
        self.assertEqual(labels.truth_ships.tolist(),[2,1])
        self.assertEqual(labels.missed_ships.tolist(),[1,0])
        pilot.raw_set_check(d,labels)

    def test_left_closed_right_open_and_publish_boundary(self):
        d = pd.DataFrame({'event_s':[0,599,600], 'arrival_s':[780,780.001,600], 'mmsi':[1,2,3]})
        labels = pilot.make_labels(d,0,1200)
        self.assertEqual(labels.seen_ships.tolist(),[1,1])
        self.assertEqual(labels.missed_ships.tolist(),[1,0])

    def test_full_delay_bound_and_repeatability(self):
        d = pd.DataFrame({'event_s':np.arange(0,86400,20),'mmsi':np.resize([211000001,211000002],4320)})
        d['event_id'] = range(len(d))
        for scenario in ['sanity','iid','burst']:
            a = pilot.simulate(d,scenario,11)
            b = pilot.simulate(d,scenario,11)
            pd.testing.assert_frame_equal(a,b)
            labels = pilot.make_labels(a,0,86400)
            if scenario == 'sanity': self.assertEqual(labels.y.sum(),0)
        self.assertTrue(pilot.simulate(d,'iid',11).out_of_order.any())
        self.assertTrue(pilot.simulate(d,'burst',11).queued.any())

    def test_future_or_pending_history_cannot_change_features(self):
        origin = int(pd.Timestamp('2022-01-01',tz='UTC').timestamp())
        s = origin+6*86400+3600
        t = s+pilot.WINDOW+pilot.PUBLISH_LAG
        d = pd.DataFrame({'event_s':[s-86400,s-86400+10,s+10],
                          'arrival_s':[s-86400,s-86400+10,s+20],
                          'mmsi':[1,1,1]})
        original = pilot.make_features(d,np.array([s]),origin,11)
        # Old-event-time record delivered after T must not enter the historical profile.
        injected = pd.DataFrame({'event_s':[s-86400+30,s+10000],
                                 'arrival_s':[t+100,t+20000], 'mmsi':[2,999]})
        altered = pd.concat([d,injected],ignore_index=True).sort_values('arrival_s')
        actual = pilot.make_features(altered,np.array([s]),origin,11)
        pd.testing.assert_frame_equal(original,actual)


if __name__ == '__main__': unittest.main()
