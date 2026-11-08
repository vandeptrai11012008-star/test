// Feather radio modem: Adafruit Feather 32u4 RFM69HCW (900 MHz) as the wristband's radio.
// The XIAO ESP32-S3 stays the brain (button, buzzer, motor, Bluetooth, the band logic); this board
// only moves packets and does the frequency hopping, so it never decides anything and never
// transmits on its own. Link to the XIAO: Serial1 (the RX / TX pins), 38400 (the 8 MHz chip cannot hit 115200 cleanly), 3.3 V logic.
//
//   XIAO -> Feather: 0x20 + 32 bytes   transmit on the current hop channel
//                    "#BURST"           the NEXT packet goes out on slot, slot+1, slot-1 (ALIVE)
//                    "#T <sec>.<ms>"    set the wall clock (the hop schedule comes from it)
//                    "#?"               status
//   Feather -> XIAO: 0x20 + 32 bytes   a packet received on the air (CRC good, exactly 32 bytes)
//                    "#RX rssi=-60"     after each packet
//                    "#OK feather radio=ok clock=set ch=12"   answer to "#?"
//
// Radio: RFM69HCW on the board, wired to the 32u4 as CS 8, IRQ (DIO0) 7, RST 4. FSK settings and
// the hop schedule are shared with every other node (include/config.h, include/hop.h).
// No clock, no transmitting: the schedule needs the time, and a transmission on a guessed channel
// would not be a hop.
#include <Arduino.h>
#include <RadioLib.h>

#include "config.h"
#include "hop.h"
#include "rescue_core.h"

#define FEATHER_CS 8
#define FEATHER_IRQ 7
#define FEATHER_RST 4
#define FEATHER_LED 13

static const uint8_t KEY[rescue::KEY_LEN] = GROUP_KEY_BYTES;

RF69 radio = new Module(FEATHER_CS, FEATHER_IRQ, FEATHER_RST, RADIOLIB_NC);
HopSchedule sched(KEY);
rescue::FrameParser parser;
bool radio_ok = false;
volatile bool rx_flag = false;
int cur_ch = 0;
bool burst_next = false;

// Wall clock: unix milliseconds at base_millis, advanced by millis(). The XIAO resends it every
// few seconds, so drift between the two boards stays far below one dwell.
uint64_t base_unix_ms = 0;
uint32_t base_millis = 0;
bool clock_ok = false;
uint64_t now_ms() { return base_unix_ms + (uint32_t)(millis() - base_millis); }

void on_rx() { rx_flag = true; }

void tune(int ch) {
  cur_ch = ch;
  radio.setFrequency(hop_freq_mhz(ch));
  radio.startReceive();
}

void hop_loop() {
  if (!radio_ok || !clock_ok) return;
  int ch = sched.channel(hop_slot(now_ms()));
  if (ch != cur_ch && !rx_flag) tune(ch);
}

void set_clock(const char* args) {  // "<seconds>.<ms>", integers only (AVR doubles are 32-bit)
  while (*args == ' ') args++;
  uint32_t sec = 0, ms = 0;
  while (*args >= '0' && *args <= '9') sec = sec * 10 + (uint32_t)(*args++ - '0');
  if (*args == '.') {
    args++;
    for (int i = 0; i < 3; i++) ms = ms * 10 + (*args >= '0' && *args <= '9' ? (uint32_t)(*args++ - '0') : 0);
  }
  base_unix_ms = (uint64_t)sec * 1000 + ms;
  base_millis = millis();
  clock_ok = sec > 1700000000UL;  // after Nov 2023
}

void transmit(const uint8_t* p) {
  radio.transmit(const_cast<uint8_t*>(p), rescue::PACKET_LEN);
}

void send_packet(const uint8_t* p) {
  if (!radio_ok || !clock_ok) return;  // silent: the XIAO sees no reply and moves on
  uint64_t slot = hop_slot(now_ms());
  if (burst_next) {  // ALIVE: the dongle may be one dwell off, so also the next and previous slot
    const int8_t offs[3] = {0, 1, -1};
    for (int i = 0; i < 3; i++) {
      radio.setFrequency(hop_freq_mhz(sched.channel(slot + offs[i])));
      transmit(p);
    }
    burst_next = false;
    tune(sched.channel(slot));
    return;
  }
  transmit(p);  // a reply goes out on the channel the challenge came in on (we are still on it)
  radio.startReceive();
}

void status() {
  Serial1.print(F("#OK feather radio="));
  Serial1.print(radio_ok ? F("ok") : F("missing"));
  Serial1.print(F(" clock="));
  Serial1.print(clock_ok ? F("set") : F("NOT-set"));
  Serial1.print(F(" ch="));
  Serial1.println(cur_ch);
}

void setup() {
  pinMode(FEATHER_LED, OUTPUT);
  Serial1.begin(FEATHER_LINK_BAUD);
  int st = radio.begin(FSK_FREQ_MHZ, FSK_BITRATE_KBPS, FSK_DEV_KHZ, FSK_RXBW_KHZ, FSK_POWER_DBM, FSK_PREAMBLE_BITS);
  if (st == RADIOLIB_ERR_NONE) st = radio.setOutputPower(FSK_POWER_DBM, RFM69_HIGH_POWER);
  if (st == RADIOLIB_ERR_NONE) {
    uint8_t sync[] = FSK_SYNC_WORD;
    st = radio.setSyncWord(sync, sizeof sync);
  }
  if (st == RADIOLIB_ERR_NONE) st = radio.setDataShaping(FSK_SHAPING);
  if (st == RADIOLIB_ERR_NONE) st = radio.setCrcFiltering(true);
  radio_ok = st == RADIOLIB_ERR_NONE;
  if (radio_ok) {
    radio.setPacketReceivedAction(on_rx);
    radio.startReceive();
  }
  Serial1.print(F("#HELLO feather radio="));
  Serial1.print(radio_ok ? F("ok") : F("missing"));
  Serial1.print(F(" code="));
  Serial1.println(st);
  digitalWrite(FEATHER_LED, radio_ok ? HIGH : LOW);  // lit = radio found
}

void loop() {
  hop_loop();
  while (Serial1.available()) {
    auto ev = parser.feed((uint8_t)Serial1.read());
    if (ev == rescue::FrameParser::PACKET) {
      send_packet(parser.packet());
    } else if (ev == rescue::FrameParser::LINE) {
      const char* l = parser.line();
      if (l[1] == 'T') set_clock(l + 2);
      else if (l[1] == 'B') burst_next = true;
      else if (l[1] == '?') status();
    }
  }
  if (rx_flag) {
    rx_flag = false;
    uint8_t buf[rescue::PACKET_LEN];
    size_t len = radio.getPacketLength();
    int st = radio.readData(buf, rescue::PACKET_LEN);
    if (st == RADIOLIB_ERR_NONE && len == rescue::PACKET_LEN) {
      Serial1.write((uint8_t)0x20);
      Serial1.write(buf, rescue::PACKET_LEN);
      Serial1.print(F("#RX rssi="));
      Serial1.println((int)radio.getRSSI());
    }
    radio.startReceive();
  }
}
