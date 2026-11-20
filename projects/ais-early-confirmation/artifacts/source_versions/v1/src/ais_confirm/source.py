from dataclasses import dataclass, field, replace
from .schema import Kind, Message, Seal, WINDOW
from . import wire

@dataclass
class LocalLog:
    records: list = field(default_factory=list)
    identities: set = field(default_factory=set)
    epoch: int = 0
    sealed: bool = False
    invalid: bool = False
    batch: set = field(default_factory=set)

class SourceState:
    """Only receives individual captured records; never holds the input trace/oracle."""
    def __init__(self, source_id, cfg, method, engine, outbox, faults=None, seed=0):
        self.source_id, self.cfg, self.method = source_id, cfg, method
        self.engine, self.outbox = engine, outbox
        self.faults, self.seed = faults or {}, seed
        self.logs, self.responses = {}, {}
        self.check_log = []
        self.capture_log = []
        self.audit = {"captures": 0, "reopens": 0, "rejected_requests": 0}
    def log(self, window):
        if window not in self.logs:
            self.logs[window] = LocalLog(epoch=1 if self.faults.get('inject_old_epoch_control_messages') else 0)
        return self.logs[window]
    def on_capture(self, record, now=None):
        window = record.event_time // WINDOW * WINDOW
        self.engine.cpu(self.source_id, {'set_key': 1, 'serialize_byte': 128}, 0,
                        self._capture_processed, window, record, self.engine.now)
    def _capture_processed(self, window, record, captured_at=None):
        log = self.log(window)
        if log.sealed:
            log.sealed = False
            log.invalid = True
            log.epoch += 1
            self.audit['reopens'] += 1
            self._send(Message(Kind.REOPEN, self.source_id, window, log.epoch,
                               payload=wire.REOPEN.pack(len(log.records))))
        seq = len(log.records) + 1
        log.records.append((seq, record.mmsi))
        self.capture_log.append((self.source_id, window, log.epoch, seq, self.engine.now if captured_at is None else captured_at,
                                 record.event_time, record.mmsi, record.event_id))
        is_new = record.mmsi not in log.identities
        log.identities.add(record.mmsi)
        if is_new and self.method == 'ID_STREAM_30S':
            log.batch.add(record.mmsi)
        body = wire.DATA.pack(record.mmsi, seq, record.event_time, record.lon, record.lat)
        body = body.ljust(self.cfg['network']['application_data_body_bytes'], b'\0')
        self.outbox(Message(Kind.DATA, self.source_id, window, log.epoch, seq, body), 'up')
        self.audit['captures'] += 1
    def on_close_timer(self, window, now=None):
        # A CPU barrier: all capture jobs queued before this timer finish before sealing.
        self.engine.cpu(self.source_id, {'set_key': 1, 'serialize_byte': 24}, 2,
                        self._close_processed, window)
    def _close_processed(self, window):
        log = self.log(window)
        if log.invalid:
            return  # no automatic reseal after a broken acquisition contract
        log.sealed = True
        if self.method == 'ID_STREAM_30S':
            self.flush_batch(window)
        if self.method in {'WM_DELTA', 'TIMER_DELTA'}:
            return
        if self.faults.get('omit_seal_source_id') != self.source_id:
            seal = Message(Kind.SEAL, self.source_id, window, log.epoch,
                           payload=wire.SEAL.pack(len(log.records), len(log.identities), window + WINDOW))
            self._send(seal)
            if self.faults.get('inject_old_epoch_control_messages'):
                self._send(replace(seal, epoch=0, payload=wire.SEAL.pack(0, 0, window + WINDOW)))
        if self.method == 'FULL_ID_MANIFEST':
            self._ids(Kind.FULL_IDS, window, log.epoch, 0, log.identities)
    def flush_batch(self, window):
        log = self.logs.get(window)
        if log and log.batch:
            pending = frozenset(log.batch)
            log.batch.clear()
            self._ids(Kind.ID_BATCH, window, log.epoch, 0, pending)
    def _send(self, message, units=None):
        costs = {'serialize_byte': len(message.payload)}
        for key, value in (units or {}).items():
            costs[key] = costs.get(key, 0) + value
        self.engine.cpu(self.source_id, costs, 4, self.outbox, message, 'up')
    def _ids(self, kind, window, epoch, request, keys):
        body = wire.encode_ids(keys)
        self._send(Message(kind, self.source_id, window, epoch, request, body),
                   {'sort_key': len(keys), 'set_key': len(keys), 'serialize_byte':5+4*len(keys)})
    def handle_check(self, request, now=None):
        if request.kind not in {Kind.CHECK_REQUEST, Kind.EXACT_FALLBACK_REQUEST} or request.query != 1 or request.roster != 1:
            self.audit['rejected_requests'] += 1
            return
        log = self.logs.get(request.window)
        if not log or not log.sealed or log.invalid or log.epoch != request.epoch:
            self.audit['rejected_requests'] += 1
            return
        ack, snapshot_size = wire.CHECK.unpack(request.payload)
        if ack > len(log.records):
            self.audit['rejected_requests'] += 1
            return
        key = (request.window, request.epoch, request.request, request.kind)
        cached = self.responses.get(key)
        if cached:
            if cached[0] != request.payload:
                self.audit['rejected_requests'] += 1
                return
            self._send(replace(cached[1], retransmission=request.retransmission))
            return
        # This uses the local log and a real contiguous acknowledgement, not future arrival times.
        pending = frozenset(mmsi for seq, mmsi in log.records if seq > ack)
        body = wire.encode_ids(pending)
        kind, extra = Kind.EXACT_IDS, {}
        if self.method == 'SEALED_DIFF_HASH' and request.kind == Kind.CHECK_REQUEST:
            from .fingerprint import select_response
            kind, body, extra = select_response(pending, snapshot_size, request, self.cfg, self.seed)
        response = Message(kind, self.source_id, request.window, log.epoch, request.request,
                           body, retransmission=request.retransmission)
        self.responses[key] = (request.payload, response)
        self.check_log.append((request.request, self.source_id, request.window, log.epoch,
                               ack, len(log.identities), snapshot_size, len(pending),
                               kind.name, len(body), self.engine.now))
        units = {'set_key': len(log.records) - ack, 'sort_key': len(pending)}
        units.update(extra)
        self._send(response, units)
