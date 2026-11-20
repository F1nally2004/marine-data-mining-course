from dataclasses import dataclass, field
from enum import IntEnum

SECOND = 1_000_000
WINDOW = 600 * SECOND
FINAL = {"EXACT_FINAL", "HASH_FINAL", "HEURISTIC_FINAL"}

class Kind(IntEnum):
    DATA = 1
    SEAL = 2
    CHECK_REQUEST = 3
    EXACT_IDS = 4
    HASH_PROOF = 5
    EXACT_FALLBACK_REQUEST = 6
    REOPEN = 7
    ID_BATCH = 8
    FULL_IDS = 9

@dataclass(frozen=True, slots=True)
class Record:
    event_id: int
    mmsi: int
    event_time: int
    lon: float
    lat: float

@dataclass(frozen=True, slots=True)
class Message:
    kind: Kind
    source: int
    window: int
    epoch: int = 0
    request: int = 0
    payload: bytes = b""
    query: int = 1
    roster: int = 1
    retransmission: int = 0

@dataclass(frozen=True, slots=True)
class Seal:
    epoch: int
    last_seq: int
    count: int
    frontier: int

@dataclass(frozen=True, slots=True)
class Snapshot:
    request: int
    window: int
    frozen_S: frozenset
    exact_K: tuple
    ack: tuple
    epochs: tuple

@dataclass(slots=True)
class WindowState:
    sources: int
    S: set = field(default_factory=set)
    K: list = field(init=False)
    seqs: list = field(init=False)
    seals: dict = field(default_factory=dict)
    epochs: list = field(init=False)
    invalid: set = field(default_factory=set)
    exact_covered: set = field(default_factory=set)
    hash_covered: dict = field(default_factory=dict)
    snapshot: object = None
    requests: dict = field(default_factory=dict)
    fallback_requested: set = field(default_factory=set)
    published: bool = False
    count: int = 0
    status: str = "PROVISIONAL"
    version: int = 0
    anchor: int = 0
    proof: str = ""
    final_identity_set: object = None
    hash_budget: float = 0.0
    identity_repairs: int = 0
    numeric_repairs: int = 0
    seal_ready_at: object = None
    check_started_at: object = None
    full_payload_at: object = None
    def __post_init__(self):
        from .snapshots import SequencePrefix
        self.K = [set() for _ in range(self.sources)]
        self.seqs = [SequencePrefix() for _ in range(self.sources)]
        self.epochs = [0] * self.sources
