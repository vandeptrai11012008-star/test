"""The console's FSK / LoRa link toggle: wording, timings, near-check numbers and the live hop channel."""
import pytest

import rescue.console as console_mod
from rescue.console import Console


@pytest.fixture(autouse=True)
def back_to_default():
    yield
    console_mod.set_link("fsk")


def test_default_is_fsk_with_a_live_hop_channel():
    console_mod.set_link("fsk")
    v = Console().view()
    assert v["link"]["id"] == "fsk" and v["link"]["short"] == "FSK"
    hop = v["hop"]
    assert 0 <= hop["channel"] < hop["channels"] == 50
    assert 903.0 <= hop["mhz"] <= 927.0 and 0 <= hop["nextIn"] <= hop["dwell"] == 8.0


def test_lora_toggle_changes_wording_numbers_and_hides_the_hop_chip():
    console_mod.set_link("lora")
    v = Console().view()
    assert v["link"]["id"] == "lora" and v["hop"] is None
    assert (console_mod.AIRTIME_MS, console_mod.BAND_PROC_MS) == (18, 7)
    assert "LoRa" in console_mod.linkup_steps()[1][2]
    console_mod.set_link("fsk")
    assert (console_mod.AIRTIME_MS, console_mod.BAND_PROC_MS) == (7, 30)
    assert "FSK" in console_mod.linkup_steps()[1][2]


@pytest.mark.parametrize("link", ["fsk", "lora"])
def test_near_check_budget_passes_direct_and_fails_a_relay(link):
    console_mod.set_link(link)
    direct = 2 * console_mod.AIRTIME_MS + console_mod.BAND_PROC_MS
    relay = direct + 2 * console_mod.AIRTIME_MS + console_mod.BAND_PROC_MS
    assert direct <= console_mod.RTT_BUDGET_MS < relay


def test_served_page_says_the_right_radio_and_has_no_leftover_tokens():
    console_mod.set_link("fsk")
    fsk = console_mod.render_html().decode()
    assert "@@" not in fsk and "FSK" in fsk and "LoRa" not in fsk and 'id="hopChip"' in fsk
    console_mod.set_link("lora")
    lora = console_mod.render_html().decode()
    assert "@@" not in lora and "LoRa 915 MHz" in lora and "903-927" not in lora


def test_preflight_reads_board_status_lines():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import preflight
    assert preflight.check_status("dog", ["#OK dog radio=ok clock=set slot=1 ch=2 auto=0"])[0] == "PASS"
    assert preflight.check_status("dog", ["#OK dog radio=ok clock=NOT-set slot=0"])[0] == "FAIL"
    assert preflight.check_status("dog", ["#OK dog radio=missing clock=set"])[0] == "FAIL"
    assert preflight.check_status("band", ["#OK feather radio=ok clock=set ch=3"])[0] == "PASS"
    assert preflight.check_status("band", ["#TEST feather asked; radio=missing"])[0] == "FAIL"
    assert preflight.check_status("band", ["#OK band radio=ok"])[0] == "FAIL"        # no Feather report
    assert preflight.check_status("dongle", ["#OK dongle radio=ok"])[0] == "PASS"
    assert preflight.check_status("dog", [])[0] == "FAIL"
