import calendar
from rescue import protocol as P
from rescue.band import Band
from rescue.interrogator import Interrogator
from rescue.radio import LoopbackRadio

KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
SLOT = calendar.timegm((2026, 11, 7, 14, 7, 0)) // 3600  # 498350, same as the web sim
NONCE = bytes([1, 2, 3, 4])


def rig(band_slot=SLOT):
    a, b = LoopbackRadio.pair()
    return Interrogator(KEY, a, lambda: SLOT), Band(KEY, lambda: band_slot), b


def test_vectors_match_web_sim():
    assert P.tag(KEY, P.CHALLENGE, NONCE, SLOT).hex() == "888adfa9b70cbdca"
    assert P.tag(KEY, P.REPLY, NONCE, SLOT).hex() == "f07c4644e68a9d2c"
    assert P.tag(KEY, P.ALIVE, NONCE, SLOT).hex() == "36261e5e924714bb"
    assert P.blink_bits(KEY, SLOT) == [1, 0, 0]


def test_packet_is_32_bytes_and_roundtrips():
    p = P.make(KEY, P.CHALLENGE, NONCE, SLOT)
    assert len(p.encode()) == 32 and P.Packet.decode(p.encode()) == p


def test_happy_path_then_alive():
    d, band, br = rig()
    d.challenge()
    br.send(band.on_packet(br.inbox.pop(0)))
    assert d.poll() == "FRIENDLY"
    br.send(band.press())
    assert d.poll() == "ALIVE"


def test_spoofed_challenge_gets_silence():
    _, band, _ = rig()
    bad = P.make(bytes(16), P.CHALLENGE, NONCE, SLOT).encode()
    assert band.on_packet(bad) is None and not band.events


def test_replayed_reply_is_unknown():
    d, band, br = rig()
    d.challenge()
    reply = band.on_packet(br.inbox.pop(0))
    br.send(reply)
    assert d.poll() == "FRIENDLY"
    br.send(reply)
    assert d.poll() == "UNKNOWN"


def test_replayed_challenge_ignored_by_band():
    d, band, br = rig()
    d.challenge()
    raw = br.inbox.pop(0)
    assert band.on_packet(raw) is not None
    assert band.on_packet(raw) is None


def test_drift_plus_minus_one_slot_ok_two_slots_not():
    for off, ok in ((1, True), (-1, True), (2, False)):
        d, band, br = rig(SLOT + off)
        d.challenge()
        assert (band.on_packet(br.inbox.pop(0)) is not None) == ok


def test_no_ping_never_friendly():
    d, _, _ = rig()
    assert d.poll() == "UNKNOWN"
