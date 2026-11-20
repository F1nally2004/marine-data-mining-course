import random
import unittest
from common import config, engine
from ais_confirm.schema import Kind, Message, WINDOW, SECOND
from ais_confirm.bloom import Bloom
from ais_confirm.fingerprint import set_fingerprint, pack_proof, verify_response, select_response
from ais_confirm.source import SourceState, LocalLog
from ais_confirm.coordinator import Coordinator
from ais_confirm import wire

class FingerprintTests(unittest.TestCase):
    def test_set_order_duplicates_and_invalid_field_embedding(self):
        p=2**127-1
        self.assertEqual(set_fingerprint([1,1,2],17,p),set_fingerprint([2,1],17,p))
        with self.assertRaises(ValueError):
            set_fingerprint([258],17,257)
    def test_no_bloom_false_negatives(self):
        keys=range(1000)
        bf=Bloom.build(keys,.01,b'x'*16)
        self.assertTrue(all(bf.contains(k) for k in keys))
    def test_bloom_all_positive_with_same_size_substitution_is_rejected(self):
        cfg=config()
        bf=Bloom(64,2,b'x'*16,bytes([255])*8)
        proof=pack_proof({1,2},bf,17,2**127-1)
        passed,risk,count,cost=verify_response(proof,frozenset({3,4}),cfg)
        self.assertEqual(count,2)
        self.assertFalse(passed)
    def test_small_field_has_nonzero_collision_and_budget_is_nonzero(self):
        cfg=config()
        cfg['proof']['fingerprint_prime_decimal']='257'
        bf=Bloom(64,2,b'x'*16,bytes([255])*8)
        roots=[]
        for r in range(257):
            passed,risk,_,_=verify_response(pack_proof({1,2},bf,r,257),frozenset({3,4}),cfg)
            if passed:
                roots.append(r)
        self.assertEqual(roots,[131])
        self.assertEqual(risk,1/257)
    def test_encoding_can_legitimately_prefer_exact_list(self):
        request=Message(Kind.CHECK_REQUEST,0,0,request=1)
        kind,body,_=select_response(frozenset(range(1000)),1000,request,config(),11)
        self.assertEqual(kind,Kind.EXACT_IDS)
        self.assertEqual(wire.decode_ids(body),frozenset(range(1000)))
    def test_full_hash_protocol_and_no_probability_to_exact_provenance_upgrade(self):
        cfg,e=config(),engine()
        keys=frozenset(random.Random(11).sample(range(200000000,800000000),1000))
        source=None
        def outbox(message,direction):
            if direction=='down':
                source.handle_check(message)
            else:
                c.receive(message)
        source=SourceState(1,cfg,'SEALED_DIFF_HASH',e,outbox,seed=11)
        source.logs[0]=LocalLog(records=list(enumerate(sorted(keys),1)),identities=set(keys),sealed=True)
        c=Coordinator('SEALED_DIFF_HASH',cfg,e,outbox,[0])
        e.now=WINDOW
        c.publish_initial(0)
        c._processed(Message(Kind.ID_BATCH,0,0,payload=wire.encode_ids(keys)))
        for i in range(2):
            c._processed(Message(Kind.SEAL,i,0,payload=wire.SEAL.pack(1000,1000,WINDOW)))
        e.run(WINDOW+10*SECOND)
        st=c.states[0]
        self.assertEqual((st.status,st.count),('HASH_FINAL',1000))
        self.assertEqual(st.K[1],set())
        self.assertEqual(c.audit['hash_checks'],1)
        self.assertGreater(st.hash_budget,0)
        self.assertLessEqual(st.hash_budget,cfg['proof']['window_error_budget'])

if __name__=='__main__':
    unittest.main()
