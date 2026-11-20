import hashlib
import math
import struct

class Bloom:
    def __init__(self, bits, hashes, salt, data=None):
        if bits < 1 or not 1 <= hashes <= 255 or len(salt) != 16:
            raise ValueError('invalid Bloom parameters')
        self.bits, self.hashes, self.salt = bits, hashes, salt
        self.data = bytearray(data) if data is not None else bytearray((bits+7)//8)
        if len(self.data) != (bits+7)//8:
            raise ValueError('invalid Bloom bit array')
    @classmethod
    def build(cls, keys, fpr, salt):
        keys = frozenset(keys)
        bits = max(1, math.ceil(-len(keys)*math.log(fpr)/(math.log(2)**2)))
        hashes = max(1, min(255, round(bits/max(1,len(keys))*math.log(2))))
        result = cls(bits, hashes, salt)
        for key in keys:
            result.add(key)
        return result
    def positions(self, key):
        digest = hashlib.blake2b(struct.pack('<I', key), key=self.salt, digest_size=16).digest()
        h1, h2 = struct.unpack('<QQ', digest)
        h2 |= 1
        for i in range(self.hashes):
            yield (h1 + i*h2) % self.bits
    def add(self, key):
        for bit in self.positions(key):
            self.data[bit//8] |= 1 << (bit%8)
    def contains_with_probes(self, key):
        probes = 0
        for bit in self.positions(key):
            probes += 1
            if not self.data[bit//8] & (1 << (bit%8)):
                return False, probes
        return True, probes
    def contains(self, key):
        return self.contains_with_probes(key)[0]
