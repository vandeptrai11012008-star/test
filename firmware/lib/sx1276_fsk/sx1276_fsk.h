// Lean SX1276 FSK driver for small MCUs (the dog's Feather 32u4: RadioLib does not fit there).
// Header only and bus-agnostic: the Bus type provides
//   void reset();  uint8_t read(uint8_t addr);  void write(uint8_t addr, uint8_t val);  uint32_t millis();
// so the same code runs on the Feather and in the host test against a simulated chip.
//
// The register image below is what RadioLib's SX1276 writes for the link settings in
// include/config.h (FSK 50 kbps, 25 kHz deviation, 125 kHz RX bandwidth, Gaussian BT 0.5,
// 32-bit preamble, sync 2D D4, variable length, CRC on, NRZ), captured by running RadioLib's
// beginFSK / setSyncWord / setDataShaping / setCrcFiltering over a simulated SPI bus that starts
// from the chip's reset values. On air this driver is therefore the same radio as the dongle's.
// Changing a link setting in config.h means recapturing this image.
#pragma once
#include <stdint.h>

namespace sx1276 {

constexpr uint8_t REG_FIFO = 0x00, REG_OPMODE = 0x01, REG_FRF_MSB = 0x06, REG_RSSI = 0x11,
                  REG_IRQ1 = 0x3E, REG_IRQ2 = 0x3F, REG_VERSION = 0x42;
constexpr uint8_t MODE_STANDBY = 0x01, MODE_TX = 0x03, MODE_RX = 0x05;  // FSK/OOK, high band
constexpr uint8_t IRQ1_MODE_READY = 0x80, IRQ2_FIFO_OVERRUN = 0x10, IRQ2_PACKET_SENT = 0x08,
                  IRQ2_PAYLOAD_READY = 0x04;

struct RegVal { uint8_t addr, val; };
// Written once after reset. 0x06..0x08 (carrier) are set per channel by set_freq_khz().
constexpr RegVal IMAGE[] = {
    {0x02, 0x02}, {0x03, 0x80},  // bit rate 50 kbps (32 MHz / 640)
    {0x04, 0x01}, {0x05, 0x99},  // deviation 25 kHz
    {0x09, 0xF0},                // PA_BOOST, 2 dBm (the chip's minimum)
    {0x0A, 0x49},                // Gaussian BT 0.5, PA ramp 40 us
    {0x0B, 0x23},                // overcurrent protection 60 mA
    {0x0D, 0x09},                // AGC on, RSSI trigger
    {0x12, 0x02}, {0x13, 0x02},  // RX bandwidth 125 kHz (and AFC bandwidth)
    {0x26, 0x04},                // preamble 4 bytes (32 bits)
    {0x27, 0xB1},                // sync on, 2 bytes
    {0x28, 0x2D}, {0x29, 0xD4},  // sync word
    {0x35, 0x9F},                // start TX when the FIFO is not empty
    {0x3F, 0x10},                // clear flags
    {0x44, 0xAD},                // fast hop
};

template <class Bus>
class Fsk {
 public:
  explicit Fsk(Bus& bus) : bus_(bus) {}

  // Reset, check the chip, write the image, go to standby. False if no SX1276 answers.
  bool begin() {
    bus_.reset();
    if (bus_.read(REG_VERSION) != 0x12) return false;
    for (const RegVal& r : IMAGE) bus_.write(r.addr, r.val);
    mode(MODE_STANDBY);
    return true;
  }

  // Carrier in kHz. Step is 61.035 Hz: FRF = kHz * 1000 / 61.035 = kHz * 2048 / 125.
  void set_freq_khz(uint32_t khz) {
    uint32_t frf = khz * 2048UL / 125UL;
    bus_.write(REG_FRF_MSB, (uint8_t)(frf >> 16));
    bus_.write(REG_FRF_MSB + 1, (uint8_t)(frf >> 8));
    bus_.write(REG_FRF_MSB + 2, (uint8_t)frf);
  }

  // Blocking send of one packet (the chip adds the length byte's framing, preamble, sync, CRC).
  bool transmit(const uint8_t* p, uint8_t n) {
    mode(MODE_STANDBY);
    bus_.write(REG_FIFO, n);  // variable length: first FIFO byte is the length
    for (uint8_t i = 0; i < n; i++) bus_.write(REG_FIFO, p[i]);
    mode(MODE_TX);
    uint32_t t0 = bus_.millis();
    bool sent = false;
    while (bus_.millis() - t0 < 50) {
      if (bus_.read(REG_IRQ2) & IRQ2_PACKET_SENT) { sent = true; break; }
    }
    mode(MODE_STANDBY);
    return sent;
  }

  void start_rx() {
    mode(MODE_STANDBY);
    bus_.write(REG_IRQ2, IRQ2_FIFO_OVERRUN);
    mode(MODE_RX);
  }

  bool rx_ready() { return bus_.read(REG_IRQ2) & IRQ2_PAYLOAD_READY; }

  // Read a received packet into buf (at most max bytes kept). Returns its length, or -1 if none.
  // The chip drops packets whose CRC fails, so anything returned passed the CRC.
  int16_t read_packet(uint8_t* buf, uint8_t max) {
    if (!rx_ready()) return -1;
    uint8_t n = bus_.read(REG_FIFO);
    for (uint8_t i = 0; i < n; i++) {
      uint8_t b = bus_.read(REG_FIFO);
      if (i < max) buf[i] = b;
    }
    return n;
  }

  uint8_t rssi_raw() { return bus_.read(REG_RSSI); }
  int16_t rssi_dbm() { return -(int16_t)(rssi_raw() / 2); }

  void mode(uint8_t m) {
    bus_.write(REG_OPMODE, m);
    uint32_t t0 = bus_.millis();
    while (!(bus_.read(REG_IRQ1) & IRQ1_MODE_READY) && bus_.millis() - t0 < 5) {}
  }

 private:
  Bus& bus_;
};

}  // namespace sx1276
