"""Real byte encodings and atomic reassembly. No partially received identity sets."""
import struct
from .schema import Kind, Message

HEADER = struct.Struct("<BBBBHHIIqQHHIII")
assert HEADER.size == 48
DATA = struct.Struct("<IQqff")
SEAL = struct.Struct("<QQq")
CHECK = struct.Struct("<QQ")
REOPEN = struct.Struct("<Q")

def encode_ids(keys):
    keys = sorted(set(keys))
    raw = struct.pack("<BI", 0, len(keys)) + (struct.pack('<' + 'I' * len(keys), *keys) if keys else b'')
    delta = bytearray(struct.pack("<BI", 1, len(keys)))
    previous = 0
    for key in keys:
        if not 0 <= key <= 0xffffffff:
            raise ValueError("identity outside uint32")
        number = key - previous
        previous = key
        while number >= 128:
            delta.append((number & 127) | 128)
            number >>= 7
        delta.append(number)
    return raw if len(raw) <= len(delta) else bytes(delta)

def decode_ids(body):
    codec, count = struct.unpack_from("<BI", body)
    if codec == 0:
        if len(body) != 5 + count * 4:
            raise ValueError("truncated identity list")
        keys = struct.unpack_from('<' + 'I' * count, body, 5) if count else ()
    elif codec == 1:
        keys, offset, previous = [], 5, 0
        for _ in range(count):
            number = shift = 0
            while True:
                if offset >= len(body) or shift > 28:
                    raise ValueError("invalid varint")
                value = body[offset]
                offset += 1
                number |= (value & 127) << shift
                if value < 128:
                    break
                shift += 7
            previous += number
            if previous > 0xffffffff:
                raise ValueError("identity overflow")
            keys.append(previous)
        if offset != len(body):
            raise ValueError("trailing identity bytes")
    else:
        raise ValueError("unknown identity codec")
    if len(set(keys)) != count or list(keys) != sorted(keys):
        raise ValueError("identity list must be sorted and unique")
    return frozenset(keys)

def frame_bytes(message, message_id, max_frame=1200):
    capacity = max_frame - HEADER.size
    parts = [message.payload[i:i+capacity] for i in range(0, len(message.payload), capacity)] or [b'']
    if len(parts) > 65535:
        raise ValueError("too many fragments")
    for index, payload in enumerate(parts):
        yield HEADER.pack(1, int(message.kind), message.retransmission, 0, message.source, message.query,
                          message.roster, message.epoch, message.window, message.request,
                          index, len(parts), len(payload), message_id, 0) + payload

def wire_size(payload_size, max_frame=1200):
    capacity = max_frame - 48
    frames = max(1, (payload_size + capacity - 1) // capacity)
    return payload_size + frames * (48 + 40)

class Reassembler:
    def __init__(self):
        self.pending = {}
        self.completed = set()
    def receive(self, frame, direction):
        v, kind, attempt, _, source, query, roster, epoch, window, req, index, total, length, mid, _ = HEADER.unpack_from(frame)
        if v != 1 or total == 0 or index >= total or len(frame) != 48 + length:
            raise ValueError("invalid frame")
        key = (direction, source, mid)
        if key in self.completed:
            return None
        scope = (kind, source, query, roster, epoch, window, req, total)
        saved_scope, pieces = self.pending.setdefault(key, (scope, {}))
        if saved_scope != scope:
            raise ValueError("mixed fragment scope")
        payload = frame[48:]
        if index in pieces and pieces[index] != payload:
            raise ValueError("conflicting duplicate fragment")
        pieces[index] = payload
        if len(pieces) != total:
            return None
        del self.pending[key]
        self.completed.add(key)
        return Message(Kind(kind), source, window, epoch, req,
                       b''.join(pieces[i] for i in range(total)), query, roster, attempt)
