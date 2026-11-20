from .schema import Kind, Message, Seal, Snapshot, WindowState, SECOND, WINDOW, FINAL
from .snapshots import cardinality_bounds
from . import wire

PUBLICATION_COLUMNS = ['window', 'time', 'version', 'count', 'status', 'cause', 'proof_kind', 'error_budget']

class Coordinator:
    def __init__(self, method, cfg, engine, outbox, windows, delta=180, initial_epochs=None):
        self.method, self.cfg, self.engine, self.outbox = method, cfg, engine, outbox
        self.n = cfg['sources']['count']
        self.states = {w: WindowState(self.n) for w in windows}
        if initial_epochs is not None:
            if len(initial_epochs)!=self.n:
                raise ValueError('registered epoch roster length mismatch')
            for state in self.states.values():
                state.epochs=list(initial_epochs)
        self.publications, self.events, self.proof_log = [], [], []
        self.next_request = 0
        self.delta = delta * SECOND
        self.last_rx = [None] * self.n
        self.max_event = [None] * self.n
        self.watermark = min(windows) - self.delta if windows else 0
        self.started_at = min(windows) if windows else 0
        self.ordered_windows = sorted(windows)
        self.watermark_cursor = 0
        self.audit = {'rejected_messages': 0, 'contract_violations': 0,
                      'safe_unseen_identity_after_exact_final': 0, 'sequence_holes_seen': 0,
                      'cross_source_duplicates': 0, 'hash_checks': 0, 'hash_failures': 0}
    def publish_initial(self, window):
        st = self.states[window]
        st.published = True
        self._publish(window, st, 'PROVISIONAL', 'window_end', max(st.anchor, len(st.S)))
        if self.method == 'WM_DELTA':
            self._advance_watermark()
        else:
            self._refresh(window, 'window_end')
    def timer_final(self, window):
        self._final(window, 'HEURISTIC_FINAL', 'timer_delta', 'HEURISTIC')
    def receive(self, message, direction='up'):
        if direction != 'up' or message.query != 1 or message.roster != 1 or message.window not in self.states or not 0 <= message.source < self.n:
            self.audit['rejected_messages'] += 1
            return
        count=1
        if message.kind in {Kind.EXACT_IDS,Kind.FULL_IDS,Kind.ID_BATCH}:
            import struct
            count=struct.unpack_from('<I',message.payload,1)[0]
        self.engine.cpu('coordinator', {'serialize_byte': len(message.payload), 'set_key': count},
                        1, self._processed, message)
    def _processed(self, msg):
        st, i = self.states[msg.window], msg.source
        if msg.epoch < st.epochs[i]:
            self.audit['rejected_messages'] += 1
            return
        if msg.kind == Kind.REOPEN:
            st.epochs[i] = msg.epoch
            self._invalidate(msg.window, i, 'source_reopen')
            return
        if msg.epoch > st.epochs[i]:
            if i in st.seals:
                self._invalidate(msg.window, i, 'epoch_advanced')
            st.epochs[i] = msg.epoch
        if msg.kind == Kind.DATA:
            mmsi, seq, event_time, _, _ = wire.DATA.unpack_from(msg.payload)
            if event_time // WINDOW * WINDOW != msg.window or msg.request != seq:
                self.audit['rejected_messages'] += 1
                return
            seal = st.seals.get(i)
            if seal and seq > seal.last_seq:
                self._invalidate(msg.window, i, 'sequence_exceeds_seal')
            if seq > st.seqs[i].prefix + 1:
                self.audit['sequence_holes_seen'] += 1
            st.seqs[i].add(seq)
            self._identities(msg.window, i, {mmsi}, 'payload')
            self.last_rx[i] = self.engine.now
            self.max_event[i] = max(event_time, self.max_event[i] or event_time)
            if self.method == 'WM_DELTA':
                self.engine.at(self.engine.now + 60 * SECOND, 3, self._advance_watermark)
                self._advance_watermark()
        elif msg.kind == Kind.SEAL:
            h, count, frontier = wire.SEAL.unpack(msg.payload)
            if frontier != msg.window + WINDOW or count > h or (i in st.seals and st.seals[i] != Seal(msg.epoch, h, count, frontier)):
                self._invalidate(msg.window, i, 'inconsistent_seal')
                return
            if st.seqs[i].maximum > h or len(st.K[i]) > count:
                self._invalidate(msg.window, i, 'seal_below_received_evidence')
                return
            st.seals[i] = Seal(msg.epoch, h, count, frontier)
            st.invalid.discard(i)
            if len(st.seals) == self.n and not st.invalid and st.seal_ready_at is None:
                st.seal_ready_at = self.engine.now
        elif msg.kind in {Kind.EXACT_IDS, Kind.FULL_IDS, Kind.ID_BATCH}:
            if msg.kind == Kind.EXACT_IDS and not self._valid_response(st, msg):
                return
            keys = wire.decode_ids(msg.payload)
            if msg.kind == Kind.EXACT_IDS:
                st.exact_covered.add(i)
            self._identities(msg.window, i, keys, 'identity_repair')
        elif msg.kind == Kind.HASH_PROOF:
            if not self._valid_response(st, msg):
                return
            self._hash_proof(msg, st)
        else:
            self.audit['rejected_messages'] += 1
            return
        self._refresh(msg.window, msg.kind.name.lower())
    def _valid_response(self, st, msg):
        snapshot = st.requests.get(msg.request)
        if not snapshot or snapshot.epochs[msg.source] != msg.epoch or msg.source not in st.seals:
            self.audit['rejected_messages'] += 1
            return False
        return True
    def _identities(self, window, i, keys, cause):
        st = self.states[window]
        new = keys - st.S
        self.audit['cross_source_duplicates'] += len((keys - st.K[i]) & st.S)
        was_final = st.status
        st.K[i].update(keys)
        st.S.update(keys)
        if cause == 'identity_repair':
            st.identity_repairs += len(new)
        if not new:
            return
        if was_final == 'EXACT_FINAL':
            # An exact numeric anchor may include identities that are not known yet.
            # Late identities in the valid sealed log are safe; evidence contradiction is not.
            if i in st.seals and len(st.K[i])<=st.seals[i].count and len(st.S)<=st.anchor:
                self.audit['safe_unseen_identity_after_exact_final'] += len(new)
        elif was_final == 'HASH_FINAL' and st.final_identity_set is not None and not keys.issubset(st.final_identity_set):
            self._invalidate(window, i, 'hash_coverage_contradicted')
        elif was_final == 'HEURISTIC_FINAL':
            self._publish(window, st, 'REOPENED', 'late_new_identity', max(st.anchor, len(st.S)))
            self._final(window, 'HEURISTIC_FINAL', 'late_revision', 'HEURISTIC')
    def _invalidate(self, window, source, cause):
        st = self.states[window]
        if source in st.invalid:
            return
        st.invalid.add(source)
        st.seals.pop(source, None)
        st.exact_covered.discard(source)
        st.hash_covered.pop(source, None)
        st.snapshot = None
        st.requests.clear()
        self.audit['contract_violations'] += 1
        self.events.append((window, self.engine.now, source, cause))
        if st.published:
            self._publish(window, st, 'REOPENED', cause, max(st.anchor, len(st.S)))
    def _refresh(self, window, cause):
        st = self.states[window]
        ready = len(st.seals) == self.n and not st.invalid
        if ready:
            try:
                lb, ub = cardinality_bounds(st.S, st.K, [st.seals[i].count for i in range(self.n)])
            except ValueError:
                self._invalidate(window, 0, 'cardinality_evidence_contradiction')
                return
            if st.status == 'EXACT_FINAL' and not lb <= st.anchor <= ub:
                self._invalidate(window, 0, 'exact_anchor_contradiction')
                return
        else:
            lb, ub = len(st.S), None
        if not st.published:
            return
        if ready and all(st.seqs[i].prefix >= st.seals[i].last_seq for i in range(self.n)):
            if st.full_payload_at is None:
                st.full_payload_at = self.engine.now
        if st.status in {'EXACT_FINAL', 'HASH_FINAL'}:
            return
        value = max(st.anchor, lb)
        if value != st.count:
            if ready and value > len(st.S):
                st.numeric_repairs += value - st.count
            self._publish(window, st, st.status, cause, value)
        if self.method in {'WM_DELTA', 'TIMER_DELTA'}:
            return
        if not ready:
            return
        if all(st.seqs[i].prefix >= st.seals[i].last_seq for i in range(self.n)):
            if st.full_payload_at is None:
                st.full_payload_at = self.engine.now
        if self.method == 'PAYLOAD_BARRIER':
            if st.full_payload_at is not None:
                self._final(window, 'EXACT_FINAL', cause, 'PAYLOAD_EXACT')
            return
        if lb == ub:
            self._final(window, 'EXACT_FINAL', 'cardinality_bounds', 'COUNT_EXACT', lb)
            return
        covered = {i for i in range(self.n) if len(st.K[i]) == st.seals[i].count} | st.exact_covered
        if len(covered) == self.n:
            self._final(window, 'EXACT_FINAL', cause, 'LIST_EXACT', len(st.S))
            return
        if len(covered | set(st.hash_covered)) == self.n:
            self._final(window, 'HASH_FINAL', cause, 'POLY_PROBABILISTIC', len(st.S))
            return
        if self.method not in {'SEALED_DIFF_EXACT', 'SEALED_DIFF_HASH'} or st.snapshot:
            return
        self.engine.at(self.engine.now, 4, self._dispatch_checks, window)
    def _dispatch_checks(self, window):
        st = self.states[window]
        if st.snapshot or st.status in FINAL or len(st.seals) != self.n or st.invalid:
            return
        self.next_request += 1
        snap = Snapshot(self.next_request, window, frozenset(st.S), tuple(frozenset(k) for k in st.K),
                        tuple(s.prefix for s in st.seqs), tuple(st.epochs))
        st.snapshot = snap
        st.requests[snap.request] = snap
        st.check_started_at = self.engine.now
        for i in range(self.n):
            if len(st.K[i]) != st.seals[i].count:
                self._send_check(window, i, snap.request, Kind.CHECK_REQUEST, 0)
    def _send_check(self, window, source, request, kind, attempt):
        st = self.states[window]
        snapshot = st.requests.get(request)
        if not snapshot or st.status in FINAL or source in st.exact_covered or source in st.hash_covered:
            return
        msg = Message(kind, source, window, snapshot.epochs[source], request,
                      wire.CHECK.pack(snapshot.ack[source], len(snapshot.frozen_S)), retransmission=attempt)
        self.engine.cpu('coordinator', {'serialize_byte': len(msg.payload)}, 4, self.outbox, msg, 'down')
        if attempt < self.cfg['network']['max_retransmissions']:
            self.engine.at(self.engine.now + self.cfg['network']['request_timeout_seconds'] * SECOND,
                           5, self._send_check, window, source, request, kind, attempt + 1)
    def _hash_proof(self, msg, st):
        from .fingerprint import verify_response
        key = (msg.source, msg.request)
        if any(row[:3] == (msg.window, msg.source, msg.request) for row in self.proof_log):
            return
        snapshot = st.requests[msg.request]
        passed, risk, candidate_n, cost = verify_response(msg.payload, snapshot.frozen_S, self.cfg)
        self.engine.cpu('coordinator', cost, 1, self._hash_checked,
                        msg, passed, risk, candidate_n)
    def _hash_checked(self, msg, passed, risk, candidate_n):
        st = self.states[msg.window]
        if not self._valid_response(st, msg):
            return
        if any(row[:3] == (msg.window, msg.source, msg.request) for row in self.proof_log):
            return
        self.audit['hash_checks'] += 1
        self.proof_log.append((msg.window, msg.source, msg.request, self.engine.now, passed, risk, candidate_n))
        if passed and st.hash_budget + risk <= self.cfg['proof']['window_error_budget']:
            st.hash_covered[msg.source] = risk
            st.hash_budget += risk
        else:
            self.audit['hash_failures'] += 1
            if msg.source not in st.fallback_requested:
                st.fallback_requested.add(msg.source)
                self._send_check(msg.window, msg.source, msg.request, Kind.EXACT_FALLBACK_REQUEST, 0)
        self._refresh(msg.window, 'hash_checked')
    def _final(self, window, status, cause, proof, count=None):
        st = self.states[window]
        if not st.published or st.status == status:
            return
        value = max(st.anchor, len(st.S)) if count is None else count
        if status == 'EXACT_FINAL':
            st.anchor = value
        st.proof = proof
        st.final_identity_set = frozenset(st.S)
        self._publish(window, st, status, cause, value)
    def _publish(self, window, st, status, cause, count):
        st.version += 1
        st.status, st.count = status, count
        self.publications.append((window, self.engine.now, st.version, count, status, cause, st.proof, st.hash_budget))
    def _advance_watermark(self):
        now = self.engine.now
        if now < self.started_at + 60*SECOND and any(t is None for t in self.last_rx):
            return
        active = [self.max_event[i] for i in range(self.n)
                  if self.last_rx[i] is not None and now - self.last_rx[i] < 60 * SECOND]
        if active:
            self.watermark = max(self.watermark, min(active) - self.delta)
        while self.watermark_cursor < len(self.ordered_windows):
            window = self.ordered_windows[self.watermark_cursor]
            st = self.states[window]
            if window + WINDOW > self.watermark or not st.published:
                break
            self.watermark_cursor += 1
            if st.status != 'HEURISTIC_FINAL':
                self._final(window, 'HEURISTIC_FINAL', 'watermark_delta', 'HEURISTIC')
