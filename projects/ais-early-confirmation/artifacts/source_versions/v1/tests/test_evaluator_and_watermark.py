import unittest
from common import config, engine
from ais_confirm.coordinator import Coordinator
from ais_confirm.schema import SECOND, WINDOW

class WatermarkTests(unittest.TestCase):
    def test_all_idle_holds_instead_of_advancing_to_infinity(self):
        e=engine()
        c=Coordinator('WM_DELTA',config(),e,lambda *x:None,[0])
        e.now=WINDOW
        c.publish_initial(0)
        watermark=c.watermark
        e.now=10*WINDOW
        c._advance_watermark()
        self.assertEqual(c.watermark,watermark)
        self.assertEqual(c.states[0].status,'PROVISIONAL')
    def test_reactivated_source_never_moves_watermark_backwards(self):
        e=engine()
        c=Coordinator('WM_DELTA',config(),e,lambda *x:None,[0])
        e.now=1000*SECOND
        c.last_rx=[e.now,None]
        c.max_event=[900*SECOND,None]
        c.publish_initial(0)
        first=c.watermark
        c.last_rx[1]=e.now
        c.max_event[1]=100*SECOND
        c._advance_watermark()
        self.assertEqual(c.watermark,first)

if __name__=='__main__':
    unittest.main()
