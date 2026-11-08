"""SDR <-> LoRa <-> UDP bridge: the drone's radio for the rescue band console.

RESPONSIBLE USE: an SDR is a radio transmitter. Only transmit where your radio regulations allow,
with any license they require, at the lowest power that works. Never jam, spoof or interfere.
This project does not support unlicensed, unlawful or unethical use. See RESPONSIBLE_USE.md.

Runs in the 'sdr' environment (GNU Radio + gr-iio + gr-lora_sdr, see sdr/README.md):

    micromamba run -n sdr python sdr/sdr_bridge.py            # real SDR
    micromamba run -n sdr python sdr/sdr_bridge.py --loopback # no radio: TX feeds RX

The console talks to it with rescue.radio.SdrRadio over localhost UDP:
    console -> bridge  udp 127.0.0.1:47701   one 32-byte packet = transmit it
    bridge -> console  udp 127.0.0.1:47702   one 32-byte packet = received off the air
                                             b"#..." text lines = status

LoRa settings match every other node: 915 MHz, SF7, BW 500 kHz, CR 4/5, explicit header,
CRC on, sync word 0x12, 8-symbol preamble. The bridge never checks packets; the console does.
"""
import argparse
import socket
import time

import pmt
from gnuradio import blocks, gr, lora_sdr

TX_PORT, RX_PORT = 47701, 47702
PACKET_LEN = 32
SF, BW, CR = 7, 500_000, 1          # gr-lora_sdr cr=1 means 4/5; 500 kHz channel
SYNC = [0x12]
SAMP_RATE = 2_000_000               # 4x oversampling of the 500 kHz LoRa signal
BUF = 16384                         # SDR buffer (samples)
MIN_TX_ATTEN_DB = 10                # ~-3 dBm max output: margin for fundamental and harmonics
PAD = 2 * BUF                       # zero samples after each frame, so the TX buffer flushes


def payload_of(msg):
    """gr-lora_sdr hands the payload over as a PMT symbol holding raw bytes."""
    raw = bytes(pmt.serialize_str(msg))
    if raw[:1] == b"\x02":          # PST_SYMBOL, u16 length, bytes
        return raw[3:3 + int.from_bytes(raw[1:3], "big")]
    return None


class Sink(gr.basic_block):
    """Message sink: every decoded LoRa payload goes to a Python callback."""

    def __init__(self, on_payload):
        gr.basic_block.__init__(self, "rescue_sink", None, None)
        self.on_payload = on_payload
        self.message_port_register_in(pmt.intern("in"))
        self.set_msg_handler(pmt.intern("in"), self.handle)   # must be a method, not a lambda

    def handle(self, msg):
        self.on_payload(payload_of(msg))


class Bridge(gr.top_block):
    def __init__(self, freq, uri, loopback, tx_atten, rx_gain, on_payload):
        gr.top_block.__init__(self, "sdr_bridge")
        # transmit chain: hex string message -> LoRa baseband (same blocks as lora_sdr_lora_tx,
        # but whitening takes hex so any byte value survives)
        self.white = lora_sdr.whitening(True, False, ",", "packet_len")
        chain = [self.white, lora_sdr.header(False, True, CR), lora_sdr.add_crc(True),
                 lora_sdr.hamming_enc(CR, SF), lora_sdr.interleaver(CR, SF, 2, BW),
                 lora_sdr.gray_demap(SF), lora_sdr.modulate(SF, SAMP_RATE, BW, SYNC, PAD, 8)]
        self.connect(*chain)
        mod = chain[-1]
        rx = lora_sdr.lora_sdr_lora_rx(int(freq), BW, CR, True, False, 255, SAMP_RATE, SF, SYNC,
                                       False, 2, [False, False])
        self.sink = Sink(on_payload)
        self.msg_connect((rx, "out"), (self.sink, "in"))
        if loopback:                 # software loop: what we send, we hear
            self.connect(mod, blocks.throttle(gr.sizeof_gr_complex, SAMP_RATE), rx)
            return
        from gnuradio import iio
        uri = uri or iio.get_pluto_uri()
        src = iio.fmcomms2_source_fc32(uri, [True, True], BUF)
        src.set_len_tag_key("packet_len")
        src.set_frequency(int(freq))
        src.set_samplerate(SAMP_RATE)
        src.set_gain_mode(0, "manual")
        src.set_gain(0, rx_gain)
        src.set_quadrature(True)
        src.set_rfdc(True)
        src.set_bbdc(True)
        src.set_filter_params("Auto", "", 0, 0)
        snk = iio.fmcomms2_sink_fc32(uri, [True, True], BUF, False)
        snk.set_len_tag_key("")
        snk.set_bandwidth(200_000 * 10)
        snk.set_frequency(int(freq))
        snk.set_samplerate(SAMP_RATE)
        snk.set_attenuation(0, tx_atten)
        snk.set_filter_params("Auto", "", 0, 0)
        self.connect(src, rx)
        self.connect(mod, snk)

    def transmit(self, packet):
        self.white.to_basic_block()._post(pmt.intern("msg"), pmt.intern(packet.hex()))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--freq", type=float, default=915.0e6)
    ap.add_argument("--uri", default="", help="SDR URI, e.g. ip:192.168.2.1 (default: find it)")
    ap.add_argument("--loopback", action="store_true", help="no radio: transmitted packets are received")
    ap.add_argument("--tx-atten", type=float, default=15.0,
                    help="TX attenuation dB (default 15, about -8 dBm; minimum 10, about -3 dBm)")
    ap.add_argument("--rx-gain", type=float, default=50.0)
    ap.add_argument("--hear-self", action="store_true",
                    help="self-test: keep our own transmissions heard by our receiver (normally dropped)")
    a = ap.parse_args()
    # Band limits with margin: stay at least 1 MHz inside 902-928 MHz, and keep the SDR at about
    # -3 dBm or less (its harmonics land in restricted bands).
    if not 903.0e6 <= a.freq <= 927.0e6:
        ap.error("--freq must be between 903 and 927 MHz (inside the 902-928 MHz band with margin)")
    if a.tx_atten < MIN_TX_ATTEN_DB:
        ap.error("--tx-atten must be at least %d dB (keeps output at about -3 dBm or less)" % MIN_TX_ATTEN_DB)

    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    console = ("127.0.0.1", RX_PORT)
    recent = {}                      # our own transmissions, to drop the echo

    def status(text):
        print(text, flush=True)
        out.sendto(text.encode(), console)

    def on_payload(p):
        if not p or len(p) != PACKET_LEN:
            return
        if not a.loopback and not a.hear_self and time.time() - recent.get(p, 0) < 2.0:
            return                   # our own TX leaking into our RX
        out.sendto(p, console)
        print("RX", p.hex(), flush=True)

    if not a.loopback:
        print("NOTICE: you are about to transmit at %.1f MHz. Make sure this frequency and power are legal "
              "where you are and that you hold any license required. See RESPONSIBLE_USE.md." % (a.freq / 1e6),
              flush=True)
    tb = Bridge(a.freq, a.uri, a.loopback, a.tx_atten, a.rx_gain, on_payload)
    tb.start()
    status("#HELLO sdr %s %.1f MHz SF%d BW%d%s" % ("loopback" if a.loopback else "radio", a.freq / 1e6, SF,
                                                   BW // 1000, " hear-self" if a.hear_self else ""))

    inp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inp.bind(("127.0.0.1", TX_PORT))
    try:
        while True:
            data, _ = inp.recvfrom(256)
            if data == b"#?":
                status("#OK sdr")
            elif len(data) == PACKET_LEN:
                recent[data] = time.time()
                tb.transmit(data)
                print("TX", data.hex(), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        tb.stop()
        tb.wait()


if __name__ == "__main__":
    main()
