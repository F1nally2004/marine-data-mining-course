import unittest
from common import config
from ais_confirm.schema import Kind, Message
from ais_confirm.wire import encode_ids, decode_ids, frame_bytes, Reassembler, wire_size

class WireTests(unittest.TestCase):
    def test_uint32_and_varint_roundtrip(self):
        for ids in [[], [1,1,2], [200000000+i for i in range(5000)], [0, 0xffffffff]]:
            encoded = encode_ids(ids)
            self.assertEqual(decode_ids(encoded), frozenset(ids))
            self.assertLessEqual(len(encoded), 5+4*len(set(ids)))
    def test_missing_middle_fragment_atomic_out_of_order_and_duplicate(self):
        body = encode_ids(range(10000))
        frames = list(frame_bytes(Message(Kind.EXACT_IDS, 0, 0, payload=body), 8))
        self.assertGreater(len(frames), 2)
        r = Reassembler()
        for frame in reversed(frames[1:]):
            self.assertIsNone(r.receive(frame, 'up'))
        self.assertIsNone(r.receive(frames[-1], 'up'))
        result = r.receive(frames[0], 'up')
        self.assertEqual(decode_ids(result.payload), frozenset(range(10000)))
        self.assertIsNone(r.receive(frames[0], 'up'))
        self.assertEqual(sum(len(f)+40 for f in frames), wire_size(len(body)))
    def test_request_retransmission_flag_survives_wire(self):
        msg = Message(Kind.CHECK_REQUEST, 0, 0, retransmission=2, payload=b'abc')
        r = Reassembler()
        out = r.receive(next(frame_bytes(msg, 9)), 'down')
        self.assertEqual(out.retransmission, 2)
    def test_truncated_identity_list_rejected(self):
        with self.assertRaises(ValueError):
            decode_ids(encode_ids([1,2,3])[:-1])
