"""The wristband / dog / dongle FSK hopping link, in Python: settings, hop schedule, framing and a
numpy GFSK modem. Pure Python + numpy, so everything here is tested without a radio.

Link settings and the hop schedule live in rescue/hop.py (read from firmware/include/config.h).

Three frame details are constants below (CRC_OUT_XOR, CRC_OVER_LENGTH, BIT1_IS_HIGH_FREQ). They were
settled on air against a real RFM69 wristband; they are not yet checked against the dog's SX1276.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rescue.hop import CONFIG_H, DEMO_KEY, HopSchedule, Link, load_link   # noqa: E402,F401  (re-exported)

CRC_INIT = 0x1D0F        # CRC-16 CCITT, polynomial 0x1021, as the RFM69 / SX1276 hardware CRC
# Found on air against a real RFM69 (wristband, 8 combinations tried, only this one was accepted):
CRC_OUT_XOR = 0xFFFF     # the chip sends the CRC inverted
CRC_OVER_LENGTH = True   # and computes it over the length byte as well as the payload
BIT1_IS_HIGH_FREQ = True  # a 1 bit is +deviation
# Not yet checked against the dog's SX1276 (same family, may match; the dog test will say).

PACKET_LEN = 32


# ---- framing ----------------------------------------------------------------------------------------
def crc16(data: bytes, init: int = CRC_INIT) -> int:
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _bits(data: bytes):
    return [(b >> (7 - i)) & 1 for b in data for i in range(8)]


def _bytes(bits) -> bytes:
    return bytes(sum(bits[i + k] << (7 - k) for k in range(8)) for i in range(0, len(bits) - 7, 8))


def frame_crc(payload: bytes) -> int:
    covered = (bytes([len(payload)]) if CRC_OVER_LENGTH else b"") + payload
    return crc16(covered) ^ CRC_OUT_XOR


def build_bits(link: Link, payload: bytes):
    """preamble | sync | length | payload | CRC16, MSB first, as the chips put it on the air."""
    body = bytes([len(payload)]) + payload + frame_crc(payload).to_bytes(2, "big")
    return [1, 0] * (link.preamble_bits // 2) + _bits(link.sync) + _bits(body)


# ---- GFSK modem ---------------------------------------------------------------------------------------
def gaussian_taps(sps: int, bt: float, span: int = 4):
    sigma = np.sqrt(np.log(2)) / (2 * np.pi * bt)            # in symbols
    t = (np.arange(span * sps + 1) - span * sps / 2) / sps
    g = np.exp(-t * t / (2 * sigma * sigma))
    return g / g.sum()


def lowpass_taps(cutoff: float, fs: float, n: int = 255):
    t = np.arange(n) - (n - 1) / 2
    h = np.sinc(2 * cutoff / fs * t) * np.hamming(n)
    return h / h.sum()


class FskModem:
    """GFSK at the link's bit rate. ``fs`` is the sample rate of the IQ going to / from the radio."""

    WORK_FS = 500_000.0      # the demodulator works at this rate (10 samples per bit)

    def __init__(self, link: Link = None, fs: float = 2_000_000.0):
        self.link = link or load_link()
        self.fs = fs
        self.decim = int(round(fs / self.WORK_FS))
        self.sps_work = int(round(self.WORK_FS / self.link.bitrate))
        self._template = None

    # transmit
    def modulate(self, payload: bytes) -> np.ndarray:
        return self.modulate_bits(build_bits(self.link, payload))

    def modulate_bits(self, bits) -> np.ndarray:
        sps = int(round(self.fs / self.link.bitrate))
        bits = np.array(bits, dtype=float)
        sign = 1.0 if BIT1_IS_HIGH_FREQ else -1.0
        nrz = np.repeat((bits * 2 - 1) * sign, sps)
        nrz = np.concatenate([np.full(2 * sps, nrz[0]), nrz, np.full(2 * sps, nrz[-1])])
        shaped = np.convolve(nrz, gaussian_taps(sps, self.link.bt), mode="same")
        phase = 2 * np.pi * np.cumsum(shaped * self.link.dev_hz) / self.fs
        return np.exp(1j * phase).astype(np.complex64)

    # receive
    def _discriminator(self, iq: np.ndarray):
        """Instantaneous frequency in Hz at WORK_FS, with the stretches where no signal is present
        zeroed (the discriminator on pure noise is huge and random). None if there is no burst."""
        x = np.convolve(iq, lowpass_taps(90_000.0, self.fs), mode="same")[:: self.decim]
        env = np.convolve(np.abs(x), np.ones(self.sps_work * 2) / (self.sps_work * 2), mode="same")
        peak = float(env.max())
        if peak <= 0.0 or peak < 3.0 * float(np.percentile(env, 10)):   # 10th percentile ~ the noise floor
            return None                                   # flat: noise only
        d = np.angle(x[1:] * np.conj(x[:-1])) * self.WORK_FS / (2 * np.pi)
        d[env[1:] < 0.4 * peak] = 0.0
        return d, env[1:] >= 0.4 * peak

    def _sync_template(self) -> np.ndarray:
        if self._template is None:
            lead = FskModem(self.link, self.WORK_FS)
            bits = np.array([1, 0] * (self.link.preamble_bits // 2) + _bits(self.link.sync), dtype=float)
            sps = self.sps_work
            sign = 1.0 if BIT1_IS_HIGH_FREQ else -1.0
            shaped = np.convolve(np.repeat((bits * 2 - 1) * sign, sps), gaussian_taps(sps, self.link.bt), mode="same")
            t = shaped * self.link.dev_hz
            self._template = t - t.mean()
        return self._template

    def decode(self, iq: np.ndarray):
        """Every valid packet found in the capture, as a list of payload bytes."""
        iq = np.asarray(iq, dtype=np.complex64)
        if len(iq) < self.decim * 600:
            return []
        got = self._discriminator(iq)
        if got is None:
            return []
        d, live = got
        d = d - (float(d[live].mean()) if live.any() else 0.0)   # remove the carrier offset
        d[~live] = 0.0
        tpl = self._sync_template()
        sps = self.sps_work
        found, start = [], 0
        while start + len(tpl) + 20 * sps < len(d):
            corr = np.correlate(d[start:], tpl, mode="valid")
            if not len(corr):
                break
            peak = int(np.argmax(np.abs(corr)))
            level = np.abs(corr[peak])
            if level < 0.25 * float(np.sum(tpl * tpl)):
                break
            at = start + peak                              # start of preamble
            frame_start = at + len(tpl)
            payload = self._slice(d, at, frame_start)
            if payload is not None:
                found.append(payload)
            start = at + len(tpl)
            if payload is not None:
                start = frame_start + (len(payload) + 3) * 8 * sps
        return found

    def _slice(self, d: np.ndarray, pre_start: int, frame_start: int):
        sps = self.sps_work
        sign = 1.0 if BIT1_IS_HIGH_FREQ else -1.0
        dc = float(np.mean(d[pre_start:frame_start]))      # carrier offset: the preamble is balanced

        def bit_at(k):
            a = frame_start + k * sps + sps // 4
            b = frame_start + k * sps + (3 * sps) // 4
            if b > len(d):
                return None
            return 1 if sign * (np.mean(d[a:b]) - dc) > 0 else 0

        def take(n, k0):
            out = []
            for k in range(k0, k0 + n):
                v = bit_at(k)
                if v is None:
                    return None
                out.append(v)
            return out

        head = take(8, 0)
        if head is None:
            return None
        n = _bytes(head)[0]
        if n == 0 or n > 64:
            return None
        rest = take((n + 2) * 8, 8)
        if rest is None:
            return None
        data = _bytes(rest)
        payload, crc = data[:n], int.from_bytes(data[n:n + 2], "big")
        return payload if crc == frame_crc(payload) else None
