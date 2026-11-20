import heapq
import itertools
import math

class Engine:
    """Integer microsecond clock; independent serialized CPUs for every endpoint."""
    def __init__(self, calibration):
        self.now = 0
        self.queue = []
        self.counter = itertools.count()
        self.cpu_ready = {}
        self.cpu_us = {}
        self.calibration = calibration
        self.events_executed = 0
    def at(self, when, priority, callback, *args):
        if when < self.now:
            raise ValueError("cannot schedule into the past")
        heapq.heappush(self.queue, (int(when), priority, next(self.counter), callback, args))
    def cpu(self, endpoint, units, priority, callback, *args):
        cost = max(1, math.ceil(sum(self.calibration[k] * v for k, v in units.items())))
        done = max(self.now, self.cpu_ready.get(endpoint, self.now)) + cost
        self.cpu_ready[endpoint] = done
        self.cpu_us[endpoint] = self.cpu_us.get(endpoint, 0) + cost
        self.at(done, priority, callback, *args)
    def run(self, until):
        while self.queue and self.queue[0][0] <= until:
            self.now, _, _, callback, args = heapq.heappop(self.queue)
            callback(*args)
            self.events_executed += 1
        self.now = until
