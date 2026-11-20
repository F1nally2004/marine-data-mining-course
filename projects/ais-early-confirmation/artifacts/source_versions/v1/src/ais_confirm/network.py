from collections import deque
import heapq
import itertools
import math
from .schema import Kind, SECOND
from .audit import uniform
from .wire import frame_bytes, Reassembler

LOG_COLUMNS = ['message_id', 'kind', 'source', 'window', 'epoch', 'request', 'fragment', 'fragments',
               'direction', 'created', 'tx_start', 'tx_end', 'rx', 'application_bytes', 'network_bytes',
               'identity_bytes', 'retransmission', 'drop']

class Network:
    def __init__(self, engine, cfg, spec, deliver):
        self.engine, self.cfg, self.spec, self.deliver = engine, cfg, spec, deliver
        self.n = cfg['sources']['count']
        self.queues = [deque() if spec['scheduler'] == 'fifo' else [] for _ in range(self.n)]
        self.busy = [False] * self.n
        self.virtual = [0.] * self.n
        self.finish_tags = [[0., 0.] for _ in range(self.n)]
        self.message_ids = itertools.count(1)
        self.ticket = itertools.count()
        self.reassembler = Reassembler()
        self.log = []
        self.outage_cache = {}
        self.frames_in_service = [None] * self.n
    def outage(self, source, hour):
        key = (source, hour)
        if key not in self.outage_cache:
            seed = int(self.spec['seed'])
            if self.spec['delay_model'] == 'burst' and uniform(seed, 'outage-trigger', source, hour) < .2:
                start = hour * 3600 * SECOND + int(uniform(seed, 'outage-start', source, hour) * 2400 * SECOND)
                self.outage_cache[key] = (start, start + 1200 * SECOND)
            else:
                self.outage_cache[key] = None
        return self.outage_cache[key]
    def transmission_finish(self, source, start, byte_count):
        needed = math.ceil(byte_count / self.cfg['network']['rate_bytes_per_second_per_source'] * SECOND)
        now = start
        # Packet transmission pauses during a source outage, including a mid-frame outage.
        while needed:
            hour = now // (3600 * SECOND)
            outage = self.outage(source, hour)
            boundary = (hour + 1) * 3600 * SECOND
            if outage and outage[0] <= now < outage[1]:
                now = outage[1]
                continue
            stop = min(boundary, outage[0]) if outage and now < outage[0] else boundary
            available = stop - now
            if needed <= available:
                return now + needed
            needed -= available
            now = stop
        return now
    def send(self, message, direction):
        mid = next(self.message_ids)
        frames = list(frame_bytes(message, mid, self.cfg['network']['max_application_frame_bytes']))
        for index, frame in enumerate(frames):
            source = message.source
            network_bytes = len(frame) + self.cfg['network']['modeled_network_overhead_bytes_per_frame']
            payload_identity_bytes = len(frame) - 48 if message.kind in {Kind.EXACT_IDS, Kind.FULL_IDS, Kind.ID_BATCH} else 0
            row = [mid, message.kind.name, source, message.window, message.epoch, message.request, index, len(frames),
                   direction, self.engine.now, None, None, None, len(frame), network_bytes,
                   payload_identity_bytes, message.retransmission, False]
            self.log.append(row)
            item = (message, direction, frame, row, network_bytes)
            if self.spec['scheduler'] == 'fifo':
                self.queues[source].append(item)
            else:
                cls = 1 if message.kind == Kind.DATA else 0
                share = .8 if cls else .2
                tag = max(self.finish_tags[source][cls], self.virtual[source]) + network_bytes / share
                self.finish_tags[source][cls] = tag
                heapq.heappush(self.queues[source], (tag, next(self.ticket), item))
            if not self.busy[source]:
                self.busy[source] = True
                self.engine.at(self.engine.now, 4, self._pump, source)
    def _pump(self, source):
        if not self.queues[source]:
            self.busy[source] = False
            return
        if self.spec['scheduler'] == 'fifo':
            item = self.queues[source].popleft()
        else:
            tag, _, item = heapq.heappop(self.queues[source])
            self.virtual[source] = max(self.virtual[source], tag)
        message, direction, frame, row, size = item
        row[10] = self.engine.now
        done = self.transmission_finish(source, self.engine.now, size)
        row[11] = done
        self.frames_in_service[source] = row
        self.engine.at(done, 1, self._transmitted, source, item)
    def _transmitted(self, source, item):
        message, direction, frame, row, size = item
        self.frames_in_service[source] = None
        seed = int(self.spec['seed'])
        fragment = row[6]
        if message.kind == Kind.DATA:
            keys = (source, message.window, message.request, fragment)
            namespace = 'data-delay'
        else:
            keys = (source, message.window, message.epoch, message.kind.name,
                    message.request, direction, message.retransmission, fragment)
            namespace = 'control-delay'
        u = uniform(seed, namespace + '-slow', *keys)
        v = uniform(seed, namespace + '-value', *keys)
        seconds = 240 + 960 * v if self.spec['delay_model'] == 'iid' and u < .1 else 20 * v
        drop_p = self.spec.get('faults', {}).get('control_packet_drop_probability', 0)
        if message.kind != Kind.DATA and uniform(seed, 'control-loss', *keys) < drop_p:
            row[17] = True
        else:
            received = self.engine.now + int(seconds * SECOND)
            row[12] = received
            self.engine.at(received, 1, self._receive, frame, direction)
        self.engine.at(self.engine.now, 4, self._pump, source)
    def _receive(self, frame, direction):
        message = self.reassembler.receive(frame, direction)
        if message is not None:
            # Retransmission attempt is a transport property, not part of proof scope.
            # Source response caching is keyed by request, independent of this counter.
            self.deliver(message, direction)
    def sent_bytes_by(self, row, deadline):
        start, finish = row[10], row[11]
        if start is None or start >= deadline:
            return 0
        if finish <= deadline:
            return row[14]
        end = min(deadline, finish)
        active = end - start
        for hour in range(start//(3600*SECOND), end//(3600*SECOND)+1):
            outage = self.outage(row[2], hour)
            if outage:
                active -= max(0, min(end, outage[1])-max(start, outage[0]))
        return min(row[14], max(0, int(active*self.cfg['network']['rate_bytes_per_second_per_source']/SECOND)))
