import json
import threading
import time

import pytest
import urllib.request

from rescue import protocol as P
import rescue.console as console_mod
from rescue.console import Console, make_server

KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
SLOT = 498350


def console():
    return Console(KEY, clock=lambda: SLOT)


def land_all(c, v):
    """Deliver packets until the channel is quiet, like the browser does."""
    while v["flight"]:
        v = c.act("deliver")
    return v


def test_starts_unknown_and_idle():
    v = console().view()
    assert v["feed"] == "UNKNOWN" and not v["busy"] and not v["canAlive"] and not v["replayReady"]
    assert v["blink"] == P.blink_bits(KEY, SLOT)


def test_challenge_reply_then_alive():
    c = console()
    v = c.act("challenge")
    assert v["busy"] and v["flight"]["to"] == "band" and v["feed"] == "UNKNOWN"
    v = c.act("deliver")                       # band verifies, buzzes, replies
    assert v["buzz"] == P.blink_bits(KEY, SLOT)
    f = v["flight"]
    assert (f["label"], f["who"], f["to"], f["dir"]) == ("AUTO REPLY", "BAND", "drone", "left")
    v = c.act("deliver")
    assert v["feed"] == "FRIENDLY" and v["exchanges"] == 1 and v["canAlive"] and not v["busy"]
    v = land_all(c, c.act("press"))
    assert v["feed"] == "ALIVE" and not v["canAlive"]


def test_spoofed_challenge_gets_silence():
    c = console()
    v = land_all(c, c.act("spoof"))
    assert v["feed"] == "UNKNOWN" and v["rejects"] == 1
    assert v["bandStatus"].startswith("SILENT") and not c.band.events and not v["canAlive"]


def test_replayed_reply_drops_feed_to_unknown():
    c = console()
    land_all(c, c.act("challenge"))
    v = land_all(c, c.act("replay"))
    assert v["feed"] == "UNKNOWN" and "replay" in v["caption"]
    assert v["insp"]["title"] == "AUTO REPLY (rejected)"


def test_old_reply_replayed_after_new_challenge_is_rejected():
    c = console()
    land_all(c, c.act("challenge"))
    old = c.captured
    c.act("challenge")                         # new nonce now outstanding, still in flight
    c.flight = None                            # drop the new challenge on the floor
    c.b_radio.inbox.clear()
    c.captured = old
    v = land_all(c, c.act("replay"))
    assert v["feed"] == "UNKNOWN" and "no outstanding challenge" in v["caption"]


def test_actions_ignored_while_packet_in_flight():
    c = console()
    c.act("challenge")
    c.act("spoof")
    assert len(c.b_radio.inbox) == 1


def test_http_api():
    server = make_server(console(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        assert b"Rescue" in urllib.request.urlopen(base + "/").read()
        post = lambda p: json.loads(urllib.request.urlopen(urllib.request.Request(base + p, method="POST")).read())
        post("/api/challenge")
        post("/api/deliver")
        assert post("/api/deliver")["feed"] == "FRIENDLY"
        state = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert state["feed"] == "FRIENDLY"
    finally:
        server.shutdown()
        server.server_close()


def test_trace_outcome_always_matches_real_decision():
    """The animated step-by-step trace must agree with Band/Interrogator in every scenario."""
    c = console()
    seen = []
    for action in ("spoof", "challenge", "replay", "press", "press", "challenge", "replay", "spoof"):
        v = c.act(action)
        while v["flight"]:
            assert v["flight"]["build"], "every packet in flight carries its build steps"
            to = v["flight"]["to"]
            v = c.act("deliver")
            t = v["trace"]
            assert t["ok"] == Console.trace_ok(t["steps"])
            if to == "band":
                assert t["ok"] == bool(v.get("buzz"))          # band verified rescue it buzzed
            else:
                assert t["ok"] == (v["feed"] != "UNKNOWN")      # drone accepted rescue feed moved
            seen.append((action, to, t["ok"]))
    assert ("spoof", "band", False) in seen and ("replay", "drone", False) in seen
    assert ("challenge", "drone", True) in seen and ("press", "drone", True) in seen


def test_failed_check_skips_the_rest():
    c = console()
    v = c.act("spoof")
    v = c.act("deliver")
    statuses = [s["status"] for s in v["trace"]["steps"]]
    bad = statuses.index("bad")
    assert v["trace"]["steps"][bad]["label"] == "Tag in slot window"
    assert all(s == "skip" for s in statuses[bad + 1:])
    chips = v["trace"]["steps"][bad]["slots"]
    assert [ch["off"] for ch in chips] == [-1, 0, 1] and all(ch["state"] == "miss" for ch in chips)


def test_reply_flight_is_sniffed_and_insp_has_raw_bytes():
    c = console()
    c.act("challenge")
    v = c.act("deliver")
    assert v["flight"]["sniffed"] and len(bytes.fromhex(v["inspNext"]["raw"])) == 32


def run_linkup(scenario):
    """Drive a link-up the way the browser does; returns the final view."""
    c = console()
    v = c.act("linkup_start", scenario)
    assert v["linkup"]["risk"] == "HIGH" and v["linkup"]["stage"] == "approach_far"
    v = land_all(c, c.act("linkup_far"))
    if v["linkup"]["stage"] == "alive":                 # normal scenario: the wearer presses I'M ALIVE
        assert v["feed"] == "FRIENDLY" and v["linkup"]["command"] == "PERSON: PRESS I'M ALIVE"
        v = land_all(c, c.act("linkup_alive"))
        assert v["feed"] == "ALIVE" and v["linkup"]["stage"] == "approach_near"
        assert next(st for st in v["linkup"]["steps"] if st["id"] == "alive")["status"] == "ok"
    if v["linkup"]["stage"] == "approach_near":
        assert v["linkup"]["risk"] == "MEDIUM" and v["feed"] in ("FRIENDLY", "ALIVE")
        c.act("dog_walk", "1500")                       # no real dog attached: a no-op
        v = c.act("linkup_near")
        assert v["flight"]["who"] == "DOG" and v["flight"]["to"] == "band"
        v = land_all(c, v)
    if v["linkup"]["stage"] == "countersign":
        assert v["linkup"]["risk"] == "LOW" and v["linkup"]["eyes"] == "green"
        v = c.act("linkup_countersign")
        assert v["linkup"]["stage"] == "closing" and v["linkup"]["countersign"] == P.blink_bits(KEY, SLOT)
        for _ in range(100):                             # poll the ultrasonic like the browser does
            v = c.act("linkup_complete")
            if v["linkup"]["stage"] != "closing":
                break
            time.sleep(0.02)
    return c, v


@pytest.fixture(autouse=True)
def fast_sim(monkeypatch):
    monkeypatch.setattr(console_mod, "COUNTERSIGN_S", 0.0)
    monkeypatch.setattr(console_mod, "SIM_CLOSE_MM_S", 1e6)


def test_linkup_normal_reaches_linked_up():
    _, v = run_linkup("normal")
    lu = v["linkup"]
    assert lu["stage"] == "complete" and lu["command"] == "LINKED UP" and not lu["running"]
    assert all(s["status"] == "ok" for s in lu["steps"])
    assert lu["near"]["ok"] and lu["near"]["rtt"] <= lu["near"]["rttMax"] and lu["near"]["rssi"] >= lu["near"]["rssiMin"]
    assert v["feed"] == "ALIVE"                          # the drone, holding at the far line; person confirmed
    assert lu["sonar"]["contact"] and lu["sonar"]["mm"] == console_mod.STOP_MM


def test_linkup_no_reply_holds_at_far_line():
    _, v = run_linkup("noreply")
    assert v["linkup"]["stage"] == "hold" and v["linkup"]["risk"] == "HIGH" and v["feed"] == "UNKNOWN"


def test_linkup_clock_drift_band_stays_silent():
    c, v = run_linkup("drift")
    assert v["linkup"]["stage"] == "hold" and not c.band.events      # real Band rejected the slot
    assert v["rejects"] == 1


def test_linkup_replay_at_far_is_rejected():
    _, v = run_linkup("replay")
    assert v["linkup"]["stage"] == "hold" and v["feed"] == "UNKNOWN"
    assert "no outstanding challenge" in v["caption"]


def test_linkup_impostor_passes_far_but_relay_is_too_slow_for_the_dog():
    c, v = run_linkup("impostor")
    lu = v["linkup"]
    assert lu["stage"] == "abort" and lu["command"].startswith("ABORT") and v["feed"] == "FRIENDLY"
    near = lu["near"]
    assert near["tag"] and near["relay"] and not near["ok"]   # genuine reply, but too slow
    assert near["rtt"] > near["rttMax"]
    assert c.dog_rescue.feed == "FRIENDLY"                       # crypto alone would have accepted it


def test_dog_trace_matches_its_decision():
    c = console()
    c.act("linkup_start", "normal")
    land_all(c, c.act("linkup_far"))
    land_all(c, c.act("linkup_alive"))
    v = c.act("linkup_near")
    v = c.act("deliver")                                      # band answers the dog
    assert v["flight"]["to"] == "dog"
    v = c.act("deliver")
    t = v["trace"]
    assert t["who"] == "DOG" and t["ok"] == Console.trace_ok(t["steps"]) and t["ok"]
    assert [s["label"] for s in t["steps"]][-2:] == ["Signal strength", "Round trip"]


def test_free_play_blocked_while_linkup_runs():
    c = console()
    c.act("linkup_start", "normal")
    c.act("challenge")
    assert not c.b_radio.inbox and c.flight is None


def test_linkup_over_http():
    server = make_server(console(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    post = lambda p: json.loads(urllib.request.urlopen(urllib.request.Request(base + p, method="POST")).read())
    try:
        assert post("/api/linkup_start/impostor")["linkup"]["scenario"] == "impostor"
    finally:
        server.shutdown()
        server.server_close()
