#pragma once
#include <Arduino.h>
#include <SPI.h>
#include "hop.h"

extern volatile bool rx_flag;
void on_rx();
constexpr int RADIO_OK = 0;

#ifdef DOG_RADIO_SX1276
#include "sx1276_fsk.h"
// Same register-level FSK driver as the existing RFM9x dog firmware.
// This preserves the band's RF69 framing, bitrate, CRC and hopping profile.
static_assert(FSK_BITRATE_KBPS == 50.0 && FSK_DEV_KHZ == 25.0 && FSK_RXBW_KHZ == 125.0,
              "SX1276 register image requires the documented FSK profile");
struct BeaconBus {
  void reset() {
    pinMode(4, OUTPUT); digitalWrite(4, LOW); delay(1);
    pinMode(4, INPUT); delay(10); // SX1276 reset is active low
  }
  uint8_t read(uint8_t address) {
    SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(8, LOW); SPI.transfer(address & 0x7f);
    uint8_t value = SPI.transfer(0); digitalWrite(8, HIGH); SPI.endTransaction();
    return value;
  }
  void write(uint8_t address, uint8_t value) {
    SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(8, LOW); SPI.transfer(address | 0x80); SPI.transfer(value);
    digitalWrite(8, HIGH); SPI.endTransaction();
  }
  uint32_t millis() { return ::millis(); }
};
static BeaconBus bus;
static sx1276::Fsk<BeaconBus> radio(bus);
inline int begin_radio() {
  pinMode(8, OUTPUT); digitalWrite(8, HIGH); SPI.begin();
  return radio.begin() ? RADIO_OK : -2;
}
inline void radio_tune(uint8_t ch) { radio.set_freq_khz((uint32_t)(hop_freq_mhz(ch) * 1000.0f + 0.5f)); }
inline void radio_listen() { radio.start_rx(); }
inline bool radio_ready() { return radio.rx_ready(); }
inline int radio_read(uint8_t* raw) { return radio.read_packet(raw, rescue::PACKET_LEN); }
inline int radio_send(const uint8_t* raw) { return radio.transmit(raw, rescue::PACKET_LEN) ? RADIO_OK : -5; }
#define BEACON_RADIO_MODEL F(" model=RFM9x-FSK code=")
#else
#include <RadioLib.h>
static RF69 radio = new Module(8, 7, 4, RADIOLIB_NC);
inline int begin_radio() {
  int st = radio.begin(FSK_FREQ_MHZ, FSK_BITRATE_KBPS, FSK_DEV_KHZ, FSK_RXBW_KHZ, FSK_POWER_DBM, FSK_PREAMBLE_BITS);
  if (st == RADIO_OK) st = radio.setOutputPower(FSK_POWER_DBM, RFM69_HIGH_POWER);
  if (st == RADIO_OK) {
    uint8_t sync[] = FSK_SYNC_WORD;
    st = radio.setSyncWord(sync, sizeof sync);
  }
  if (st == RADIO_OK) st = radio.setDataShaping(FSK_SHAPING);
  if (st == RADIO_OK) st = radio.setCrcFiltering(true);
  if (st == RADIO_OK) radio.setPacketReceivedAction(on_rx);
  return st;
}
inline void radio_tune(uint8_t ch) { radio.setFrequency(hop_freq_mhz(ch)); }
inline void radio_listen() { radio.startReceive(); }
inline bool radio_ready() { return rx_flag; }
inline int radio_read(uint8_t* raw) {
  size_t len = radio.getPacketLength();
  int st = radio.readData(raw, rescue::PACKET_LEN);
  return st == RADIO_OK ? (int)len : st;
}
inline int radio_send(const uint8_t* raw) { return radio.transmit(raw, rescue::PACKET_LEN); }
#define BEACON_RADIO_MODEL F(" model=RFM69HCW code=")
#endif
