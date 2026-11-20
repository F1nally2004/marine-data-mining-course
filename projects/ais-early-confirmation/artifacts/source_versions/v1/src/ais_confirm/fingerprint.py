import hashlib
import struct
from .schema import Kind
from .bloom import Bloom
from . import wire

PROOF_HEAD = struct.Struct('<QIB16s')

def set_fingerprint(keys, r, p):
    value = 1
    for key in set(keys):
        if not 0 <= key < p:
            raise ValueError('identities must be injectively embedded in the field')
        value = value*(r-key) % p
    return value

def scope_digest(seed, namespace, request, counter=0):
    text = '|'.join(map(str, (seed, namespace, request.source, request.window,
                              request.epoch, request.request, counter))).encode()
    return hashlib.blake2b(text, digest_size=16).digest()

def uniform_field_point(seed, request, p):
    counter = 0
    # Rejection sampling, not biased uint128 % p. Independent Bloom and point namespaces.
    while True:
        candidate = int.from_bytes(scope_digest(seed, 'fingerprint-point', request, counter), 'little')
        if candidate < p:
            return candidate
        counter += 1

def pack_proof(keys, bloom, r, p):
    return (PROOF_HEAD.pack(len(set(keys)), bloom.bits, bloom.hashes, bloom.salt) +
            r.to_bytes(16,'little') + set_fingerprint(keys,r,p).to_bytes(16,'little') + bytes(bloom.data))

def select_response(pending, snapshot_size, request, cfg, seed):
    exact = wire.encode_ids(pending)
    n = len(pending)
    costs = {'serialize_byte': 5+4*n}  # unselected uint32 candidate is also constructed
    if n == 0 or n > snapshot_size:
        return Kind.EXACT_IDS, exact, costs
    fpr = min(cfg['proof']['bloom_base_fpr'], cfg['proof']['bloom_expected_extra_candidate_budget']/max(1,snapshot_size-n))
    bloom = Bloom.build(pending, fpr, scope_digest(seed, 'bloom-salt', request))
    p = int(cfg['proof']['fingerprint_prime_decimal'])
    r = uniform_field_point(seed, request, p)
    proof = pack_proof(pending,bloom,r,p)
    costs.update({'bloom_key':n,'bloom_probe':n*bloom.hashes,'modmul':n})
    if wire.wire_size(len(exact)) <= wire.wire_size(len(proof)):
        costs['serialize_byte'] += len(proof)
        return Kind.EXACT_IDS, exact, costs
    costs['serialize_byte'] += len(exact)
    return Kind.HASH_PROOF, proof, costs

def verify_response(payload, frozen_S, cfg):
    n,bits,hashes,salt = PROOF_HEAD.unpack_from(payload)
    offset = PROOF_HEAD.size
    if len(payload) != offset + 32 + (bits+7)//8:
        raise ValueError('truncated hash proof')
    r = int.from_bytes(payload[offset:offset+16],'little')
    fingerprint = int.from_bytes(payload[offset+16:offset+32],'little')
    p = int(cfg['proof']['fingerprint_prime_decimal'])
    if r >= p or fingerprint >= p:
        raise ValueError('invalid field element')
    bloom = Bloom(bits,hashes,salt,payload[offset+32:])
    candidate, probes = set(), 0
    for key in frozen_S:
        contains, used = bloom.contains_with_probes(key)
        probes += used
        if contains:
            candidate.add(key)
    passed = len(candidate) == n and set_fingerprint(candidate,r,p) == fingerprint
    risk = max(0,n-1)/p
    return passed, risk, len(candidate), {'bloom_key':len(frozen_S),'bloom_probe':probes,
                                          'modmul':len(candidate) if len(candidate)==n else 0}
