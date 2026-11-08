// Dongle firmware: USB serial <-> radio passthrough for the laptop (the drone's radio).
// LoRa by default; with -DLINK_FSK (env dongle_fsk) the SX1276 runs FSK to match the RFM69 wristband.
// Same framing as the band's USB mode, so rescue.radio.SerialLoRaRadio works with either.
//   host -> dongle: 0x20 + 32 bytes = transmit;  "#?" = identify
//   dongle -> host: 0x20 + 32 bytes = received;  "#RX rssi=.. snr=.." after each packet
//                   (FSK has no SNR: it prints snr=0.0)
// FSK + hopping: the channel is a keyed schedule of the wall-clock dwell slot (include/hop.h), the
// same one the band computes. A host packet goes out on the current slot, then the next, then the
// previous; the dongle follows the schedule while idle. Needs #T from the laptop first.
// The dongle never checks packets: the drone software verifies everything.
#include <Arduino.h>
#include <RadioLib.h>
#include <sys/time.h>
#include <time.h>

#include "config.h"
#include "rescue_core.h"
#include "hop.h"

SX1276 radio = new Module(PIN_LORA_CS, PIN_LORA_DIO0, PIN_LORA_RST, PIN_LORA_DIO1);
rescue::FrameParser parser;
bool radio_ok = false;
volatile bool rx_flag = false;

#ifdef LINK_FSK
#define LINK_SNR() 0.0f
#else
#define LINK_SNR() radio.getSNR()
#endif

void IRAM_ATTR on_rx() { rx_flag = true; }

void forward(const uint8_t* buf) {  // a packet from the air to the laptop
  Serial.write((uint8_t)0x20);
  Serial.write(buf, rescue::PACKET_LEN);
  Serial.printf("#RX rssi=%.0f snr=%.1f\n", radio.getRSSI(), LINK_SNR());
}

#ifdef FSK_HOP
static const uint8_t KEY[rescue::KEY_LEN] = GROUP_KEY_BYTES;
HopSchedule sched(KEY);
int cur_ch = 0;

bool clock_set() { return time(nullptr) > 1700000000; }  // set by #T from the laptop
uint64_t now_ms() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (uint64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}
void tune(int ch) {
  cur_ch = ch;
  radio.setFrequency(hop_freq_mhz(ch));
  radio.startReceive();
}

// Transmit p on the channel of dwell slot `slot`, then listen up to FSK_REPLY_WAIT_MS for a
// 32-byte packet into buf.
bool try_slot(uint64_t slot, const uint8_t* p, uint8_t* buf) {
  cur_ch = sched.channel(slot);
  radio.setFrequency(hop_freq_mhz(cur_ch));
  radio.transmit(const_cast<uint8_t*>(p), rescue::PACKET_LEN);
  radio.startReceive();
  rx_flag = false;  // the end-of-transmit edge may have set it
  uint32_t t0 = millis();
  while (millis() - t0 < FSK_REPLY_WAIT_MS) {
    if (!rx_flag) { yield(); continue; }
    rx_flag = false;
    size_t len = radio.getPacketLength();
    int st = radio.readData(buf, rescue::PACKET_LEN);
    radio.startReceive();
    if (st == RADIOLIB_ERR_NONE && len == rescue::PACKET_LEN) return true;
  }
  return false;
}

// Current dwell slot first, then the next, then the previous (the clocks may be a little apart).
// No clock, no transmitting.
bool exchange(const uint8_t* p, uint8_t* buf) {
  if (!clock_set()) {
    Serial.println("#WARN clock not set, send #T <unix>");
    return false;
  }
  uint64_t slot = hop_slot(now_ms());
  const int64_t offs[3] = {0, 1, -1};
  for (int i = 0; i < 3; i++) {
    if (try_slot(slot + offs[i], p, buf)) return true;
  }
  Serial.println("#NOREPLY");
  return false;
}

// Idle: follow the schedule so the band's ALIVE lands on the channel we are listening on.
void hop_loop() {
  if (!radio_ok || !clock_set()) return;
  int ch = sched.channel(hop_slot(now_ms()));
  if (ch != cur_ch && !rx_flag) tune(ch);
}
#endif

void setup() {
  Serial.begin(115200);
#ifdef LINK_FSK
  int st = radio.beginFSK(FSK_FREQ_MHZ, FSK_BITRATE_KBPS, FSK_DEV_KHZ, FSK_RXBW_KHZ, FSK_DONGLE_POWER_DBM, FSK_PREAMBLE_BITS);
  if (st == RADIOLIB_ERR_NONE) {
    uint8_t sync[] = FSK_SYNC_WORD;
    st = radio.setSyncWord(sync, sizeof sync);
  }
  if (st == RADIOLIB_ERR_NONE) st = radio.setDataShaping(FSK_SHAPING);
  if (st == RADIOLIB_ERR_NONE) st = radio.setCrcFiltering(true);
#else
  int st = radio.begin(LORA_FREQ_MHZ, LORA_BW_KHZ, LORA_SF, LORA_CR, RADIOLIB_SX127X_SYNC_WORD, LORA_POWER_DBM);
#endif
  radio_ok = st == RADIOLIB_ERR_NONE;
  if (radio_ok) {
    radio.setPacketReceivedAction(on_rx);
    radio.startReceive();
  }
  Serial.printf("#HELLO dongle radio=%s (code %d)\n", radio_ok ? "ok" : "missing", st);
}

void loop() {
#ifdef FSK_HOP
  hop_loop();
#endif
  while (Serial.available()) {
    auto ev = parser.feed((uint8_t)Serial.read());
    if (ev == rescue::FrameParser::PACKET && radio_ok) {
#ifdef FSK_HOP
      uint8_t reply[rescue::PACKET_LEN];
      if (exchange(parser.packet(), reply)) forward(reply);
#else
      radio.transmit(const_cast<uint8_t*>(parser.packet()), rescue::PACKET_LEN);
      radio.startReceive();
#endif
    } else if (ev == rescue::FrameParser::LINE && parser.line()[1] == '?') {
      Serial.printf("#OK dongle radio=%s\n", radio_ok ? "ok" : "missing");
#ifdef FSK_HOP
    } else if (ev == rescue::FrameParser::LINE && parser.line()[1] == 'T') {
      double t = atof(parser.line() + 2);  // seconds with milliseconds: the schedule needs them
      struct timeval tv = {(time_t)t, (suseconds_t)((t - (double)(time_t)t) * 1e6)};
      settimeofday(&tv, nullptr);
      Serial.printf("#TIME %llu\n", (unsigned long long)hop_slot(now_ms()));
#endif
    }
  }

  if (rx_flag) {
    rx_flag = false;
    uint8_t buf[rescue::PACKET_LEN];
    size_t len = radio.getPacketLength();
    int st = radio.readData(buf, rescue::PACKET_LEN);
    if (st == RADIOLIB_ERR_NONE && len == rescue::PACKET_LEN) {
      forward(buf);
    }
    radio.startReceive();
  }
}
