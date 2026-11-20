import unittest
from dataclasses import replace
from common import config, engine
from ais_confirm.schema import Kind, Message, Record, WINDOW, SECOND
from ais_confirm.coordinator import Coordinator
from ais_confirm.source import SourceState
from ais_confirm.snapshots import SequencePrefix, cardinality_bounds
from ais_confirm import wire

class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.e = engine()
        self.sent = []
        self.c = Coordinator('SEALED_DIFF_EXACT', config(), self.e, lambda *x: self.sent.append(x), [0])
        self.e.now = WINDOW
        self.c.publish_initial(0)
    def data(self, source, seq, identity, epoch=0):
        self.c._processed(Message(Kind.DATA, source, 0, epoch, seq,
                                 wire.DATA.pack(identity, seq, 1, 0., 0.).ljust(128, b'\0')))
    def seal(self, source, last, count, epoch=0):
        self.c._processed(Message(Kind.SEAL, source, 0, epoch,
                                 payload=wire.SEAL.pack(last, count, WINDOW)))
    def test_exact_numeric_anchor_accepts_unseen_sealed_identities(self):
        self.data(0, 1, 1)
        self.data(1, 1, 1)
        self.seal(0, 3, 3)
        self.seal(1, 1, 1)
        st = self.c.states[0]
        self.assertEqual((st.status, st.count, len(st.S)), ('EXACT_FINAL', 3, 1))
        before = list(self.c.publications)
        self.data(0, 3, 3)  # out of order and never seen, but within sealed log
        self.data(0, 2, 2)
        self.assertEqual(self.c.publications, before)
        self.assertEqual((st.count, len(st.S), st.seqs[0].prefix), (3, 3, 3))
        self.assertEqual(self.c.audit['safe_unseen_identity_after_exact_final'], 2)
        self.assertEqual(self.c.audit['contract_violations'], 0)
    def test_out_of_seal_sequence_invalidates_even_duplicate_identity(self):
        self.seal(0, 1, 1)
        self.seal(1, 0, 0)
        self.assertEqual(self.c.states[0].count, 1)
        self.data(0, 2, 1)
        self.assertEqual(self.c.states[0].status, 'REOPENED')
        self.assertEqual(self.c.audit['contract_violations'], 1)
    def test_cross_source_duplicate_union(self):
        self.data(0, 1, 1)
        self.data(0, 2, 2)
        self.data(1, 1, 2)
        self.data(1, 2, 3)
        self.seal(0, 2, 2)
        self.seal(1, 2, 2)
        self.assertEqual(self.c.states[0].count, 3)
        self.assertEqual(self.c.states[0].status, 'EXACT_FINAL')
        self.assertEqual(self.c.audit['cross_source_duplicates'], 1)
    def test_count_only_ambiguous_overlap_does_not_sum(self):
        self.seal(0, 2, 2)
        self.seal(1, 2, 2)
        st = self.c.states[0]
        self.assertEqual(st.status, 'PROVISIONAL')
        self.assertEqual(cardinality_bounds(st.S, st.K, [2, 2]), (2, 4))
    def test_missing_empty_seal_stays_unknown(self):
        self.seal(0, 0, 0)
        self.assertEqual(self.c.states[0].status, 'PROVISIONAL')
        self.e.run(WINDOW + 7200 * SECOND)
        self.assertFalse(any(p[4] == 'EXACT_FINAL' for p in self.c.publications))
    def test_legal_empty_seals_confirm_zero(self):
        self.seal(0, 0, 0)
        self.seal(1, 0, 0)
        self.assertEqual((self.c.states[0].status, self.c.states[0].count), ('EXACT_FINAL', 0))
    def test_sequence_hole_differs_from_payload_barrier(self):
        self.c.method = 'PAYLOAD_BARRIER'
        self.data(0, 2, 1)
        self.seal(0, 2, 1)
        self.seal(1, 0, 0)
        self.assertEqual(self.c.states[0].status, 'PROVISIONAL')
        self.data(0, 1, 1)
        self.assertEqual(self.c.states[0].status, 'EXACT_FINAL')
    def test_exact_difference_recovers_unique_identity_then_payload_is_idempotent(self):
        self.data(0, 1, 1)
        self.seal(0, 2, 2)
        self.seal(1, 1, 1)
        self.c._dispatch_checks(0)
        req = self.c.states[0].snapshot.request
        self.c._processed(Message(Kind.EXACT_IDS, 0, 0, 0, req, wire.encode_ids({2})))
        self.c._processed(Message(Kind.EXACT_IDS, 1, 0, 0, req, wire.encode_ids({3})))
        st = self.c.states[0]
        self.assertEqual((st.status, st.count, st.identity_repairs), ('EXACT_FINAL', 3, 2))
        before = len(self.c.publications)
        self.data(0, 2, 2)
        self.data(1, 1, 3)
        self.assertEqual(len(self.c.publications), before)
        self.assertEqual(st.count, 3)
    def test_stale_epoch_wrong_query_roster_and_unsolicited_proof(self):
        self.seal(0, 0, 0, epoch=1)
        self.seal(0, 4, 4, epoch=0)
        self.c.receive(Message(Kind.SEAL, 1, 0, query=2))
        self.c.receive(Message(Kind.SEAL, 1, 0, roster=2))
        self.c._processed(Message(Kind.EXACT_IDS, 1, 0, request=999, payload=wire.encode_ids({1})))
        self.assertEqual(self.c.states[0].seals[0].count, 0)
        self.assertEqual(self.c.audit['rejected_messages'], 4)
    def test_anchor_retained_as_lower_bound_after_reopen(self):
        self.seal(0, 3, 3)
        self.seal(1, 0, 0)
        self.c._processed(Message(Kind.REOPEN, 0, 0, epoch=1, payload=wire.REOPEN.pack(3)))
        self.assertEqual((self.c.states[0].status, self.c.states[0].count), ('REOPENED', 3))
    def test_registered_epoch_rejects_old_seal_even_if_it_arrives_first(self):
        c=Coordinator('SEALED_DIFF_EXACT',config(),self.e,lambda *x:None,[0],initial_epochs=[1,1])
        c.publish_initial(0)
        c._processed(Message(Kind.SEAL,0,0,0,payload=wire.SEAL.pack(0,0,WINDOW)))
        self.assertEqual(c.states[0].seals,{})
        self.assertEqual(c.audit['rejected_messages'],1)
    def test_heuristic_late_identity_revises_and_retains_previous_claim(self):
        self.c.method = 'TIMER_DELTA'
        self.c.timer_final(0)
        self.data(0, 1, 1)
        statuses = [p[4] for p in self.c.publications]
        self.assertEqual(statuses, ['PROVISIONAL', 'HEURISTIC_FINAL', 'REOPENED', 'HEURISTIC_FINAL'])
        self.assertEqual(self.c.states[0].count, 1)

class LocalSourceTests(unittest.TestCase):
    def test_unsealed_baselines_do_not_emit_reopen_on_late_capture(self):
        for method in ['WM_DELTA','TIMER_DELTA']:
            with self.subTest(method=method):
                e,sent=engine(),[]
                src=SourceState(0,config(),method,e,lambda m,d:sent.append(m))
                src._close_processed(0)
                src._capture_processed(0,Record(1,123,1,0.,0.))
                e.run(SECOND)
                self.assertFalse(src.logs[0].sealed)
                self.assertEqual([m.kind for m in sent],[Kind.DATA])
                self.assertEqual(src.audit['reopens'],0)
    def test_baseline_late_capture_still_revises_heuristic_count(self):
        e=engine()
        e.now=WINDOW+180*SECOND
        c=Coordinator('TIMER_DELTA',config(),e,lambda *x:None,[0])
        c.publish_initial(0)
        c.timer_final(0)
        src=SourceState(0,config(),'TIMER_DELTA',e,lambda m,d:c.receive(m))
        src._close_processed(0)
        src._capture_processed(0,Record(1,123,1,0.,0.))
        e.run(WINDOW+181*SECOND)
        self.assertEqual((c.states[0].status,c.states[0].count),('HEURISTIC_FINAL',1))
        self.assertEqual(c.audit['contract_violations'],0)
    def test_tail_set_uses_contiguous_ack_not_max_sequence(self):
        e, sent = engine(), []
        src = SourceState(0, config(), 'SEALED_DIFF_EXACT', e, lambda *m: sent.append(m))
        for i, key in enumerate([1, 2, 1]):
            src._capture_processed(0, Record(i, key, i+1, 0, 0))
        src._close_processed(0)
        req = Message(Kind.CHECK_REQUEST, 0, 0, request=7, payload=wire.CHECK.pack(1, 1))
        src.handle_check(req)
        src.handle_check(replace(req, retransmission=1))
        e.run(SECOND)
        responses = [m for m, _ in sent if m.kind == Kind.EXACT_IDS]
        self.assertEqual([wire.decode_ids(m.payload) for m in responses], [frozenset({1,2})]*2)
        self.assertEqual(responses[0].payload, responses[1].payload)
        self.assertEqual(responses[1].retransmission, 1)
    def test_post_seal_capture_emits_reopen_and_does_not_auto_reseal(self):
        e, sent = engine(), []
        src = SourceState(0, config(), 'SEALED_DIFF_EXACT', e, lambda *m: sent.append(m))
        src._close_processed(0)
        src._capture_processed(0, Record(1, 123, 1, 0, 0))
        src._close_processed(0)
        e.run(SECOND)
        self.assertTrue(src.logs[0].invalid)
        self.assertEqual(sum(m.kind == Kind.SEAL for m, _ in sent), 1)
        self.assertEqual(sum(m.kind == Kind.REOPEN for m, _ in sent), 1)
    def test_sequence_prefix(self):
        seq = SequencePrefix()
        for i in [4,2,2,1]:
            seq.add(i)
        self.assertEqual((seq.prefix, seq.maximum), (2,4))
        seq.add(3)
        self.assertEqual(seq.prefix, 4)

if __name__ == '__main__':
    unittest.main()
