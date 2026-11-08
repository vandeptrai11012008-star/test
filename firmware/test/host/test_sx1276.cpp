// Runs the lean SX1276 FSK driver against a simulated chip and checks it leaves the registers the
// way RadioLib's SX1276 does for the same settings (the table below was captured from RadioLib).
#include <stdio.h>
#include <string.h>

#include "sx1276_fsk.h"

static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

struct SimBus {
  uint8_t regs[128];
  uint8_t tx_fifo[80]; int tx_n = 0;     // what went into the FIFO
  uint8_t rx_fifo[80]; int rx_n = 0, rx_pos = 0;  // what the "air" gives us
  uint32_t t = 0;
  uint8_t last_mode = 0;
  bool version_ok = true;
  bool fifo_after_tx_mode = false;
  uint8_t tx_len_on_air = 0; uint8_t sent[80];

  SimBus() { reset(); }
  void reset() {
    memset(regs, 0, sizeof regs);
    const uint8_t rst[][2] = {{0x01,0x01},{0x02,0x1A},{0x03,0x0B},{0x04,0x00},{0x05,0x52},{0x06,0x6C},{0x07,0x80},
      {0x09,0x4F},{0x0A,0x09},{0x0B,0x2B},{0x0C,0x20},{0x0D,0x08},{0x0E,0x02},{0x0F,0x0A},{0x10,0xFF},{0x12,0x15},
      {0x13,0x0B},{0x1F,0xAA},{0x24,0x05},{0x26,0x03},{0x27,0x93},{0x28,0x01},{0x29,0x01},{0x30,0x90},{0x31,0x40},
      {0x32,0x40},{0x35,0x0F},{0x44,0x2D},{0x4D,0x84}};
    for (auto& r : rst) regs[r[0]] = r[1];
    tx_n = 0; rx_n = rx_pos = 0;
  }
  uint32_t millis() { return t += 1; }
  uint8_t read(uint8_t a) {
    if (a == 0x42) return version_ok ? 0x12 : 0x00;
    if (a == 0x3E) return 0x80;                                    // mode ready
    if (a == 0x3F) {                                               // flags
      uint8_t f = 0;
      if (last_mode == 0x03) f |= 0x08;                            // packet sent
      if (last_mode == 0x05 && rx_pos < rx_n) f |= 0x04;           // payload ready
      return f;
    }
    if (a == 0x00) return rx_pos < rx_n ? rx_fifo[rx_pos++] : 0;
    return regs[a];
  }
  void write(uint8_t a, uint8_t v) {
    if (a == 0x00) { tx_fifo[tx_n++] = v; return; }
    if (a == 0x01) {
      last_mode = v;
      if (v == 0x03) { memcpy(sent, tx_fifo, tx_n); tx_len_on_air = tx_n ? tx_fifo[0] : 0; tx_n = 0; }
    }
    regs[a] = v;
  }
};

int main() {
  SimBus bus; sx1276::Fsk<SimBus> radio(bus);
  CHECK(radio.begin());

  // Captured from RadioLib (beginFSK 903.1 MHz / 50 kbps / 25 kHz / 125 kHz / 2 dBm / 32-bit preamble,
  // sync 2D D4, shaping BT 0.5, CRC on) over a simulated SX1276 starting at its reset values.
  const uint8_t want[][2] = {{0x02,0x02},{0x03,0x80},{0x04,0x01},{0x05,0x99},{0x09,0xF0},{0x0A,0x49},
    {0x0B,0x23},{0x0D,0x09},{0x12,0x02},{0x13,0x02},{0x26,0x04},{0x27,0xB1},{0x28,0x2D},{0x29,0xD4},
    {0x35,0x9F},{0x44,0xAD},{0x30,0x90},{0x31,0x40},{0x32,0x40}};
  for (auto& w : want) { if (bus.regs[w[0]] != w[1]) printf("reg 0x%02X = 0x%02X, want 0x%02X\n", w[0], bus.regs[w[0]], w[1]); CHECK(bus.regs[w[0]] == w[1]); }
  CHECK(bus.last_mode == 0x01);  // standby

  radio.set_freq_khz(903100);    // RadioLib: E1 C6 66
  CHECK(bus.regs[0x06] == 0xE1 && bus.regs[0x07] == 0xC6 && bus.regs[0x08] >= 0x65 && bus.regs[0x08] <= 0x67);
  radio.set_freq_khz(926620);
  uint32_t frf = ((uint32_t)bus.regs[6] << 16) | (bus.regs[7] << 8) | bus.regs[8];
  double mhz = frf * 32.0 / (1 << 19);
  CHECK(mhz > 926.619 && mhz < 926.621);

  // transmit: length byte first, then the 32 bytes, then TX mode
  uint8_t pkt[32]; for (int i = 0; i < 32; i++) pkt[i] = (uint8_t)(i * 7 + 1);
  CHECK(radio.transmit(pkt, 32));
  CHECK(bus.tx_len_on_air == 32 && memcmp(bus.sent + 1, pkt, 32) == 0);
  CHECK(bus.last_mode == 0x01);

  // receive: nothing waiting, then a good 32-byte packet, then a wrong-length one
  radio.start_rx();
  CHECK(bus.last_mode == 0x05);
  uint8_t buf[32];
  CHECK(radio.read_packet(buf, 32) == -1);
  bus.rx_n = 0; bus.rx_pos = 0; bus.rx_fifo[bus.rx_n++] = 32; for (int i = 0; i < 32; i++) bus.rx_fifo[bus.rx_n++] = pkt[i];
  CHECK(radio.rx_ready());
  CHECK(radio.read_packet(buf, 32) == 32 && memcmp(buf, pkt, 32) == 0);
  bus.rx_n = 0; bus.rx_pos = 0; bus.rx_fifo[bus.rx_n++] = 20; for (int i = 0; i < 20; i++) bus.rx_fifo[bus.rx_n++] = 0xAA;
  CHECK(radio.read_packet(buf, 32) == 20);       // caller rejects anything that is not 32

  bus.version_ok = false;                         // no chip: begin() fails
  CHECK(!radio.begin());

  puts(fails ? "sx1276: FAILED" : "sx1276: ok");
  return fails ? 1 : 0;
}
