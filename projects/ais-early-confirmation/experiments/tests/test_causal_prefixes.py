import unittest
from common import config, engine
from ais_confirm.schema import Record, WINDOW, SECOND, Kind
from ais_confirm.source import SourceState
from ais_confirm.coordinator import Coordinator
from ais_confirm.network import Network

def replay(extra=False):
    e, cfg = engine(), config()
    sources=[]
    def deliver(msg,direction):
        if direction=='up':
            c.receive(msg)
        else:
            sources[msg.source].handle_check(msg)
    n = Network(e,cfg,dict(seed=11,scheduler='fifo',delay_model='iid',faults={}),deliver)
    c = Coordinator('SEALED_DIFF_EXACT',cfg,e,n.send,[0,WINDOW])
    sources=[SourceState(i,cfg,'SEALED_DIFF_EXACT',e,n.send) for i in range(2)]
    for i in range(2):
        e.at(SECOND,0,sources[i].on_capture,Record(i,1,SECOND,0.,0.))
        e.at(WINDOW+30*SECOND,2,sources[i].on_close_timer,0)
    e.at(WINDOW,3,c.publish_initial,0)
    if extra:
        e.at(3000*SECOND,0,sources[0].on_capture,Record(999,999,WINDOW+SECOND,0.,0.))
    e.run(2000*SECOND)
    return c.publications, c.states[0].S, n.log

class CausalTests(unittest.TestCase):
    def test_future_injection_cannot_change_prefix(self):
        self.assertEqual(replay(False),replay(True))
    def test_deterministic_replay(self):
        self.assertEqual(replay(False),replay(False))
    def test_source_and_coordinator_have_no_oracle_or_each_other(self):
        e,cfg=engine(),config()
        s=SourceState(0,cfg,'SEALED_DIFF_EXACT',e,lambda *x:None)
        c=Coordinator('SEALED_DIFF_EXACT',cfg,e,lambda *x:None,[0])
        for instance in [s,c]:
            self.assertFalse(any('oracle' in name or 'truth' in name or 'dataframe' in name for name in vars(instance)))
        self.assertFalse(any(isinstance(value,SourceState) for value in vars(c).values()))

if __name__ == '__main__':
    unittest.main()
