// Dog radio firmware: Adafruit Feather 32u4 RFM9x (900 MHz) riding on the MechDog.
// It waits for a signed DOG_CHECK order from the drone, challenges the wristband itself at close
// range, measures signal strength and round trip, and sends back a signed DOG_VERDICT.
// Packet formats: rescue/protocol.py.
//
//   drone --DOG_CHECK--> dog --CHALLENGE--> band --REPLY--> dog --DOG_VERDICT--> drone
//
// Radio: the SX1276 in FSK mode (lib/sx1276_fsk), the same link as the wristband and the dongle:
// 50 kbps FSK, keyed hop schedule from the group key and the wall clock (include/hop.h). RadioLib
// does not fit in 28 KB, so the driver is a small register-level one checked against RadioLib.
//
// Clock: the hop schedule needs the time to about a second, and the dog has no clock of its own.
// Set it over USB: "#T <unix>.<ms>" (the laptop does this at setup, like the wristband). With no
// clock the dog does not hop, listen for orders or transmit. Drift is a few seconds a day on this
// chip; set it again for long sessions. The hour for signing still comes from each order.
//
// USB (115200, bench only; nothing on USB is needed to run):
//   "#T <sec>.<ms>"  set the clock      "#?" or "?"  status
//   "#N"             run a near check now against the wristband (random order), no drone needed
//   "#A <seconds>"   run a near check by itself every N seconds (0 = off). RAM only: a reboot clears it.
//                    For bench tests with the USB cable pulled; the long LED flash means verified.
//   0x20 + 32 bytes  a signed order, handled like one received over the air
#include <Arduino.h>
#include <SPI.h>

#include "config.h"
#include "hop.h"
#include "rescue_core.h"
#include "sx1276_fsk.h"

// Feather 32u4 RFM9x: the radio is wired on the board.
#define DOG_CS 8
#define DOG_IRQ 7
#define DOG_RST 4
#define DOG_LED 13

// Near check thresholds: keep equal to rescue/console.py (RSSI_MIN_DBM, RTT_BUDGET_MS).
// RTT: FSK airtime is ~7 ms each way, but the wristband answers through a serial radio modem
// (~30 ms). Not measured on the real pair yet.
#define RSSI_MIN_DBM -70
#define RTT_BUDGET_MS 65

static const uint8_t KEY[rescue::KEY_LEN] = GROUP_KEY_BYTES;

struct DogBus {
  void reset() {
    pinMode(DOG_RST, OUTPUT);
    digitalWrite(DOG_RST, LOW);
    delay(1);
    pinMode(DOG_RST, INPUT);  // released: the chip comes up in 5 ms
    delay(10);
  }
  uint8_t read(uint8_t a) {
    SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(DOG_CS, LOW);
    SPI.transfer(a & 0x7F);
    uint8_t v = SPI.transfer(0);
    digitalWrite(DOG_CS, HIGH);
    SPI.endTransaction();
    return v;
  }
  void write(uint8_t a, uint8_t v) {
    SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
    digitalWrite(DOG_CS, LOW);
    SPI.transfer(a | 0x80);
    SPI.transfer(v);
    digitalWrite(DOG_CS, HIGH);
    SPI.endTransaction();
  }
  uint32_t millis() { return ::millis(); }
};

DogBus bus;
sx1276::Fsk<DogBus> radio(bus);
HopSchedule sched(KEY);
bool radio_ok = false;
uint32_t slot = 0;                 // adopted from the last valid order (or the clock)
uint8_t last_order[rescue::NONCE_LEN];
bool have_order = false;
uint16_t auto_period_s = 0;       // bench: self-triggered near checks, 0 = off
uint32_t auto_last_ms = 0;

// Wall clock: unix milliseconds at base_millis, advanced by millis().
uint64_t base_unix_ms = 0;
uint32_t base_millis = 0;
bool clock_ok = false;
int cur_ch = -1;
uint64_t now_ms() { return base_unix_ms + (uint32_t)(millis() - base_millis); }

void led(bool on) { digitalWrite(DOG_LED, on ? HIGH : LOW); }

void tune(uint8_t ch) {
  if ((int)ch == cur_ch) return;
  cur_ch = ch;
  radio.set_freq_khz((uint32_t)(hop_freq_mhz(ch) * 1000.0f + 0.5f));
}

// Idle: follow the schedule and listen.
void hop_loop() {
  if (!radio_ok || !clock_ok) return;
  uint8_t ch = sched.channel(hop_slot(now_ms()));
  if ((int)ch != cur_ch) {
    tune(ch);
    radio.start_rx();
  }
}

void set_clock(const char* args) {  // "<sec>.<ms>", integers only (AVR doubles are 32-bit)
  while (*args == ' ') args++;
  uint32_t sec = 0, ms = 0;
  while (*args >= '0' && *args <= '9') sec = sec * 10 + (uint32_t)(*args++ - '0');
  if (*args == '.') {
    args++;
    for (int i = 0; i < 3; i++) ms = ms * 10 + (*args >= '0' && *args <= '9' ? (uint32_t)(*args++ - '0') : 0);
  }
  base_unix_ms = (uint64_t)sec * 1000 + ms;
  base_millis = millis();
  clock_ok = sec > 1700000000UL;
  if (clock_ok && sec / rescue::SLOT_SECONDS > slot) slot = sec / rescue::SLOT_SECONDS;
  cur_ch = -1;  // retune on the next loop
}

// The packet to send goes on the current slot's channel and the next and previous ones, so a
// clock a few seconds off still lands on the channel the other end is listening on.
void send_burst(const uint8_t p[rescue::PACKET_LEN]) {
  uint64_t s = hop_slot(now_ms());
  const int8_t offs[3] = {0, 1, -1};
  for (int i = 0; i < 3; i++) {
    tune(sched.channel(s + offs[i]));
    radio.transmit(p, rescue::PACKET_LEN);
  }
  cur_ch = -1;
}

// The 32u4 has no hardware RNG, so mix several weak sources with SHA-256: the drone's order
// nonce (random from the laptop), the microsecond timer, a counter, radio noise (RSSI register)
// and floating analog pins. Every nonce is unique (counter + order) and unpredictable without
// the order and the exact timing.
void fresh_nonce(const uint8_t order[rescue::NONCE_LEN], uint8_t out[rescue::NONCE_LEN]) {
  static uint8_t pool[32];
  static uint32_t counter = 0;
  uint8_t buf[32 + rescue::NONCE_LEN + 4 + 4 + 8 + 6];
  uint8_t* p = buf;
  memcpy(p, pool, 32); p += 32;
  memcpy(p, order, rescue::NONCE_LEN); p += rescue::NONCE_LEN;
  uint32_t us = micros(), c = ++counter;
  memcpy(p, &us, 4); p += 4;
  memcpy(p, &c, 4); p += 4;
  for (uint8_t i = 0; i < 8; i++) { *p++ = radio.rssi_raw(); delayMicroseconds(37 + (us & 31)); }
  const uint8_t pins[6] = {A0, A1, A2, A3, A4, A5};
  for (uint8_t i = 0; i < 6; i++) *p++ = (uint8_t)analogRead(pins[i]);
  rescue::sha256(buf, sizeof buf, pool);
  memcpy(out, pool, rescue::NONCE_LEN);
}

// Send the challenge on one channel and listen for the band's reply. The strongest RSSI seen while
// listening is kept: after the packet ends the register falls back to noise.
bool try_channel(uint8_t ch, const uint8_t chal[rescue::PACKET_LEN], const uint8_t nonce[rescue::NONCE_LEN],
                 bool* tag_ok, int8_t* rssi, uint16_t* rtt) {
  tune(ch);
  uint32_t t_start = millis();
  radio.transmit(chal, rescue::PACKET_LEN);
  radio.start_rx();
  int16_t peak = -127;
  while (millis() - t_start < FSK_REPLY_WAIT_MS) {
    int16_t r = radio.rssi_dbm();
    if (r > peak) peak = r;
    if (!radio.rx_ready()) continue;
    uint8_t buf[rescue::PACKET_LEN];
    uint32_t t_rx = millis();
    int16_t n = radio.read_packet(buf, rescue::PACKET_LEN);
    radio.start_rx();
    rescue::Packet p;
    if (n != (int16_t)rescue::PACKET_LEN || !rescue::decode(buf, rescue::PACKET_LEN, p) ||
        p.type != rescue::REPLY || memcmp(p.nonce, nonce, rescue::NONCE_LEN) != 0)
      continue;                    // someone else's packet: keep listening
    *tag_ok = rescue::verify(KEY, p, slot, nullptr);
    *rssi = (int8_t)constrain(peak, -128, 127);
    *rtt = (uint16_t)(t_rx - t_start);
    return true;
  }
  return false;
}

void near_check(const uint8_t order[rescue::NONCE_LEN]) {
  if (!clock_ok) return;
  led(true);
  uint8_t nonce[rescue::NONCE_LEN], pkt[rescue::PACKET_LEN];
  fresh_nonce(order, nonce);
  rescue::encode(rescue::make(KEY, rescue::CHALLENGE, nonce, slot), pkt);

  bool tag_ok = false;
  int8_t rssi = -128;
  uint16_t rtt = rescue::NO_REPLY_MS;
  uint64_t s = hop_slot(now_ms());
  const int8_t offs[3] = {0, 1, -1};
  for (int i = 0; i < 3; i++) {
    if (try_channel(sched.channel(s + offs[i]), pkt, nonce, &tag_ok, &rssi, &rtt)) break;
  }
  bool ok = tag_ok && rssi >= RSSI_MIN_DBM && rtt <= RTT_BUDGET_MS;
  rescue::make_dog_verdict(KEY, order, slot, ok, tag_ok, rssi, 0, rtt, pkt);  // FSK has no SNR: 0
  delay(20);                       // let the band's radio go back to receive
  send_burst(pkt);
  Serial.print(F("#CHECK nonce="));
  for (uint8_t i = 0; i < rescue::NONCE_LEN; i++) { if (nonce[i] < 16) Serial.print('0'); Serial.print(nonce[i], HEX); }
  Serial.print(F(" ok=")); Serial.print(ok);
  Serial.print(F(" tag=")); Serial.print(tag_ok);
  Serial.print(F(" rssi=")); Serial.print(rssi);
  Serial.print(F(" snr=0"));
  Serial.print(F(" rtt=")); Serial.println(rtt == rescue::NO_REPLY_MS ? -1 : (long)rtt);
  led(false);
  if (ok) { delay(100); led(true); delay(400); led(false); }  // long flash: verified
  radio.start_rx();
}

// A valid order: adopt its hour and run the near check. Same rules wherever the order came from.
void handle_order(const uint8_t buf[rescue::PACKET_LEN]) {
  uint8_t order[rescue::NONCE_LEN];
  uint32_t s;
  if (!rescue::parse_dog_check(KEY, buf, rescue::PACKET_LEN, order, &s)) return;    // silent on anything else
  if (have_order && memcmp(order, last_order, rescue::NONCE_LEN) == 0) return;   // replayed order
  if (s < slot) return;                                                       // never move back in time
  slot = s;
  memcpy(last_order, order, rescue::NONCE_LEN);
  have_order = true;
  near_check(order);
}

void status() {
  Serial.print(F("#OK dog radio=")); Serial.print(radio_ok ? F("ok") : F("missing"));
  Serial.print(F(" clock=")); Serial.print(clock_ok ? F("set") : F("NOT-set"));
  Serial.print(F(" slot=")); Serial.print(slot);
  Serial.print(F(" ch=")); Serial.print(cur_ch);
  Serial.print(F(" auto=")); Serial.println(auto_period_s);
}

void setup() {
  Serial.begin(115200);
  pinMode(DOG_LED, OUTPUT);
  pinMode(DOG_CS, OUTPUT);
  digitalWrite(DOG_CS, HIGH);
  SPI.begin();
  radio_ok = radio.begin();
  for (int i = 0; i < (radio_ok ? 2 : 6); i++) { led(true); delay(80); led(false); delay(120); }
  Serial.print(F("#HELLO dog radio=")); Serial.println(radio_ok ? F("ok") : F("missing"));
}

void loop() {
  hop_loop();
  // USB (bench only): lines starting '#', '?', or 0x20 + 32 bytes = a signed order.
  static uint8_t usb[rescue::PACKET_LEN];
  static int8_t usb_n = -1;
  static char line[28];
  static int8_t line_n = -1;
  while (Serial.available()) {
    int c = Serial.read();
    if (usb_n >= 0) {
      usb[usb_n++] = (uint8_t)c;
      if (usb_n == (int8_t)rescue::PACKET_LEN) { usb_n = -1; handle_order(usb); }
    } else if (line_n >= 0) {
      if (c == '\n' || c == '\r') {
        line[line_n] = 0;
        line_n = -1;
        if (line[0] == 'T') set_clock(line + 1);
        else if (line[0] == 'N') { uint8_t o[rescue::NONCE_LEN] = {(uint8_t)micros(), (uint8_t)(micros() >> 8), 0x44, 0x4F}; near_check(o); }
        else if (line[0] == 'A') { auto_period_s = (uint16_t)atoi(line + 1); auto_last_ms = millis(); }
        else if (line[0] == '?') status();
      } else if (line_n < (int8_t)sizeof line - 1) {
        line[line_n++] = (char)c;
      }
    } else if (c == 0x20) {
      usb_n = 0;
    } else if (c == '#') {
      line_n = 0;
    } else if (c == '?') {
      status();
    }
  }
  if (auto_period_s && clock_ok && millis() - auto_last_ms >= (uint32_t)auto_period_s * 1000UL) {
    auto_last_ms = millis();
    uint8_t o[rescue::NONCE_LEN] = {(uint8_t)micros(), (uint8_t)(micros() >> 8), 0x41, 0x55};
    near_check(o);
  }
  if (radio_ok && clock_ok && radio.rx_ready()) {
    uint8_t buf[rescue::PACKET_LEN];
    int16_t n = radio.read_packet(buf, rescue::PACKET_LEN);
    radio.start_rx();
    if (n == (int16_t)rescue::PACKET_LEN) handle_order(buf);
  }
}
