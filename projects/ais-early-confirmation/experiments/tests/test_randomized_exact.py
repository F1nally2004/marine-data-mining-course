import random
import unittest
from common import config, engine
from ais_confirm.schema import Kind, Message, WINDOW
from ais_confirm.coordinator import Coordinator
from ais_confirm import wire

class RandomizedExactTests(unittest.TestCase):
    def test_two_hundred_multisource_permutations_preserve_exact_anchors(self):
        rng=random.Random(20261119)
        for case in range(200):
            sets=[set(rng.sample(range(1,13),rng.randrange(13))) for _ in range(3)]
            truth=len(set.union(*sets))
            e=engine()
            c=Coordinator('UPSTREAM_COUNT_ONLY',config(3),e,lambda *x:None,[0])
            e.now=WINDOW
            c.publish_initial(0)
            messages=[]
            for source,keys in enumerate(sets):
                log=sorted(keys)+rng.choices(sorted(keys),k=5) if keys else []
                for seq,key in enumerate(log,1):
                    messages.append(Message(Kind.DATA,source,0,request=seq,
                                            payload=wire.DATA.pack(key,seq,1,0.,0.).ljust(128,b'\0')))
                messages.append(Message(Kind.SEAL,source,0,payload=wire.SEAL.pack(len(log),len(keys),WINDOW)))
            rng.shuffle(messages)
            for message in messages:
                e.now+=1
                c._processed(message)
                if c.states[0].status=='EXACT_FINAL':
                    self.assertEqual(c.states[0].count,truth,(case,message))
            self.assertEqual(c.states[0].status,'EXACT_FINAL',case)
            self.assertEqual(c.audit['contract_violations'],0,case)
            self.assertFalse(any(row[4]=='REOPENED' for row in c.publications),case)

if __name__=='__main__':
    unittest.main()
