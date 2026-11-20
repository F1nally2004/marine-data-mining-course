import unittest
from common import config, engine
from ais_confirm.schema import Message, Kind, SECOND
from ais_confirm.network import Network

class NetworkTests(unittest.TestCase):
    def setup_net(self, scheduler='fifo', delay='iid'):
        e = engine()
        got = []
        cfg = config()
        spec = dict(seed=11, scheduler=scheduler, delay_model=delay, faults={})
        return e, Network(e, cfg, spec, lambda m,d: got.append((m,d))), got
    def test_fifo_shares_bidirectional_capacity_and_charges_headers(self):
        e,n,got = self.setup_net()
        n.send(Message(Kind.DATA, 0, 0, request=1, payload=bytes(128)), 'up')
        n.send(Message(Kind.CHECK_REQUEST, 0, 0, payload=bytes(16)), 'down')
        e.run(2000*SECOND)
        self.assertEqual(len(got), 2)
        self.assertEqual([r[14] for r in n.log], [216,104])
        self.assertGreaterEqual(n.log[1][10], n.log[0][11])
        self.assertEqual(sum(n.sent_bytes_by(r, e.now) for r in n.log), 320)
    def test_data_and_control_both_blocked_by_same_outage(self):
        e,n,_ = self.setup_net(delay='burst')
        n.outage_cache[0,0] = (0,1200*SECOND)
        for kind in [Kind.DATA, Kind.SEAL]:
            n.send(Message(kind, 0, 0, payload=bytes(24)), 'up')
        e.run(1199*SECOND)
        self.assertEqual(sum(n.sent_bytes_by(r,e.now) for r in n.log),0)
        e.run(1500*SECOND)
        self.assertTrue(all(r[11]>=1200*SECOND for r in n.log))
    def test_wfq_is_work_conserving_and_control_gets_service(self):
        e,n,_ = self.setup_net(scheduler='control_wfq')
        for j in range(40):
            n.send(Message(Kind.DATA, 0, 0, request=j, payload=bytes(128)), 'up')
        n.send(Message(Kind.SEAL, 0, 0, payload=bytes(24)), 'up')
        e.run(2000*SECOND)
        order = sorted(n.log,key=lambda r:r[10])
        control_index = next(i for i,r in enumerate(order) if r[1]=='SEAL')
        self.assertLess(control_index,40)
        for left,right in zip(order,order[1:]):
            self.assertEqual(left[11],right[10])
    def test_control_loss_cannot_be_interpreted_as_empty(self):
        e,n,got = self.setup_net()
        n.spec['faults']['control_packet_drop_probability']=1.
        n.send(Message(Kind.SEAL,0,0,payload=bytes(24)), 'up')
        e.run(2000*SECOND)
        self.assertEqual(got,[])
        self.assertTrue(n.log[0][17])
    def test_extra_controls_change_queue_but_not_exogenous_data_propagation(self):
        logs=[]
        for extra in [False,True]:
            e,n,_=self.setup_net()
            if extra:
                n.send(Message(Kind.SEAL,0,0,payload=bytes(24)),'up')
            n.send(Message(Kind.DATA,0,0,request=1,payload=bytes(128)),'up')
            e.run(2000*SECOND)
            logs.append(next(r for r in n.log if r[1]=='DATA'))
        self.assertGreater(logs[1][10],logs[0][10])
        self.assertEqual(logs[0][12]-logs[0][11],logs[1][12]-logs[1][11])

if __name__ == '__main__':
    unittest.main()
