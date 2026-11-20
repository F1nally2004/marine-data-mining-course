class SequencePrefix:
    """Track actual contiguous delivery, never max(seq) as an acknowledgement."""
    __slots__ = ("prefix", "holes", "maximum")
    def __init__(self):
        self.prefix = 0
        self.maximum = 0
        self.holes = set()
    def add(self, seq):
        if seq < 1:
            raise ValueError("sequence must start at 1")
        self.maximum = max(self.maximum, seq)
        if seq > self.prefix:
            self.holes.add(seq)
            while self.prefix + 1 in self.holes:
                self.prefix += 1
                self.holes.remove(self.prefix)

def cardinality_bounds(known, source_known, counts):
    if any(len(k) > n for k, n in zip(source_known, counts)):
        raise ValueError("known source identities exceed sealed cardinality")
    lower = max([len(known), *counts])
    upper = len(known) + sum(n - len(k) for n, k in zip(counts, source_known))
    if lower > upper:
        raise ValueError("inconsistent sealed identity evidence")
    return lower, upper
