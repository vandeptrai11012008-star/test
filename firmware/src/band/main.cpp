// Band firmware: port of rescue/band.py for ESP32 + RFM95W (LoRa), and the XIAO ESP32-S3 wristband
// with an RFM69HCW (FSK, RADIO_RFM69 + LINK_FSK; the RFM69 has no LoRa mode).
// Host link over USB serial and Bluetooth LE ("Rescue-Band-xxxx"), same framing on both.
// (env band_xiao). Pins: include/config.h.
//
// Verified challenge: buzzer chirp, then the 3-pulse blink code on the LED and the
// vibration motor (SHORT 150 ms, LONG 450 ms). Button: send ALIVE.
//
// Listens on LoRa AND on USB serial (USB test mode, so one node can be tested from the
// laptop before a second radio exists). A reply goes back the way the challenge came.
// Over the air the band is completely silent on any failure. The "#..." text lines are
// a USB-only debug channel and are never transmitted.
//
// USB commands:  #T <unix>  set the clock (the slot comes from it)
//                #P         press the button (send ALIVE)
//                #?         identify ("#OK band")
//   hardware self-test, USB only (never over the air):
//                #L  LED 1 s     #B  buzzer 0.3 s     #M  motor 0.3 s
//                #C  play blink code 101 on everything     #R  radio status
//   the button logs "#BTN down" / "#BTN up" as it changes
#include <Arduino.h>
#include <RadioLib.h>
#include <SPI.h>
#include "driver/gpio.h"
#include <sys/time.h>
#include <time.h>

#include "config.h"
#include "rescue_core.h"
#include "periodic_link.h"
#include "checkin_button.h"
#include "hop.h"

rescue::PeriodicBand* periodic_band = nullptr;
bool last_was_periodic = false;
uint64_t pairing_id = 0;
rescue::CheckinButton checkin_button;
bool cue_active = false, feather_led_ready = false;
uint32_t cue_started = 0;
extern int button_pressed_level;
#if defined(RADIO_FEATHER)
#define BAND_FEATHER 1  // radio on a Feather 32u4 RFM69 over Serial1 (firmware/src/feather)
#endif
#if BAND_BLE
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <freertos/queue.h>
#endif

static const uint8_t KEY[rescue::KEY_LEN] = GROUP_KEY_BYTES;

// ---- host link: USB serial and (optionally) Bluetooth LE, same framing on both ------------
// Bluetooth is a Nordic-UART-style service: the laptop writes to RX, we notify on TX.
// Over Bluetooth the "#SILENT" and "#WARN" debug lines are never sent, so a stranger nearby
// learns nothing from a rejected packet (rule 2: silent on any failure).
#if BAND_BLE
#define NUS_SERVICE "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_RX "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
#define NUS_TX "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
BLECharacteristic* ble_tx = nullptr;
volatile bool ble_connected = false;
QueueHandle_t ble_in = nullptr;                 // bytes written by the laptop
portMUX_TYPE ble_mux = portMUX_INITIALIZER_UNLOCKED;
std::string ble_out;                            // bytes waiting to be notified
rescue::FrameParser ble_parser;

class BleConn : public BLEServerCallbacks {
  void onConnect(BLEServer*) override { ble_connected = true; }
  void onDisconnect(BLEServer* s) override { ble_connected = false; s->getAdvertising()->start(); }
};
class BleRx : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c) override {
    std::string v = c->getValue();
    for (char ch : v) xQueueSend(ble_in, &ch, 0);
  }
};
#endif

class HostOut : public Print {
 public:
  size_t write(uint8_t b) override {
    Serial.write(b);
#if BAND_BLE
    if (ble_connected) ble_byte(b);
#endif
    return 1;
  }
  size_t write(const uint8_t* buf, size_t n) override {
    for (size_t i = 0; i < n; i++) write(buf[i]);
    return n;
  }

 private:
#if BAND_BLE
  enum { IDLE, PACKET, LINE } st_ = IDLE;
  int left_ = 0;
  std::string line_;
  void push(const std::string& d) { portENTER_CRITICAL(&ble_mux); ble_out += d; portEXIT_CRITICAL(&ble_mux); }
  void ble_byte(uint8_t b) {                    // follow the framing so we can drop whole lines
    if (st_ == PACKET) { push(std::string(1, (char)b)); if (--left_ == 0) st_ = IDLE; return; }
    if (st_ == LINE) {
      line_ += (char)b;
      if (b == '\n') {
        if (line_.rfind("#SILENT", 0) != 0 && line_.rfind("#WARN", 0) != 0) push(line_);
        line_.clear();
        st_ = IDLE;
      }
      return;
    }
    if (b == 0x20) { st_ = PACKET; left_ = rescue::PACKET_LEN; push(std::string(1, (char)b)); }
    else if (b == '#') { st_ = LINE; line_ = "#"; }
  }
#endif
};
HostOut Out;

#if defined(BAND_FEATHER)
// no radio object here: the radio is on the Feather, reached over Serial1
#elif defined(RADIO_RFM69)
RF69 radio = new Module(PIN_LORA_CS, PIN_LORA_DIO0, PIN_LORA_RST, PIN_LORA_DIO1, SPI);
#define REG_VERSION 0x10   // RFM69 RegVersion reads 0x24
#define REG_VERSION_OK 0x24
#define REG_RSSI 0x24
#else
SX1276 radio = new Module(PIN_LORA_CS, PIN_LORA_DIO0, PIN_LORA_RST, PIN_LORA_DIO1, SPI);
#define REG_VERSION 0x42   // SX1276 RegVersion reads 0x12
#define REG_VERSION_OK 0x12
#define REG_RSSI 0x1B
#endif
rescue::BandCore band(KEY);
rescue::FrameParser parser;
bool radio_ok = false;
volatile bool rx_flag = false;

enum Medium { USB_LINK, AIR };
Medium last_medium = USB_LINK;  // ALIVE goes back the way the last verified challenge came

void IRAM_ATTR on_rx() { rx_flag = true; }

#ifdef BAND_FEATHER
// Radio on the Feather (include/config.h: PIN_FEATHER_TX/RX). The Feather hops and moves packets;
// every decision stays here. It needs the clock for the hop schedule, so we resend it every 5 s.
rescue::FrameParser feather_parser;
uint32_t feather_clock_at = 0;

uint64_t now_ms() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (uint64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

void feather_clock() {
  if (time(nullptr) <= 1700000000) return;  // clock not set yet
  uint64_t ms = now_ms();
  Serial1.printf("#T %lu.%03lu\n", (unsigned long)(ms / 1000), (unsigned long)(ms % 1000));
  feather_clock_at = millis();
}
#endif

bool clock_set();

// FSK hopping (include/hop.h, include/config.h): the channel is a keyed schedule of the wall-clock
// dwell slot, the same one the dongle computes. No clock, no transmitting: the band stays on
// channel 0 and every packet fails the slot check anyway.
#if defined(RADIO_RFM69) && defined(FSK_HOP)
#define BAND_HOP 1
HopSchedule sched(KEY);
int cur_ch = 0;

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

void hop_loop(bool radio_ok_now) {
  if (!radio_ok_now || !clock_set()) return;
  int ch = sched.channel(hop_slot(now_ms()));
  if (ch != cur_ch && !rx_flag) tune(ch);  // never hop with a packet waiting to be read
}
#endif

bool clock_set() { return time(nullptr) > 1700000000; }  // after Nov 2023: set by #T
uint32_t now_slot() { return (uint32_t)(time(nullptr) / rescue::SLOT_SECONDS); }

void usb_packet(const uint8_t p[rescue::PACKET_LEN]) {
  Out.write((uint8_t)0x20);
  Out.write(p, rescue::PACKET_LEN);
}

void air_packet(const uint8_t p[rescue::PACKET_LEN], bool burst = false) {
  if (!radio_ok) return;
#if defined(BAND_HOP) || defined(BAND_FEATHER)
  if (!clock_set()) return;  // hopping needs the clock: never transmit on a guessed channel
#endif
#ifdef LINK_FSK
  static uint32_t last_tx = 0;  // airtime cap: drop (silently) a send that follows too soon
  if (last_tx && millis() - last_tx < FSK_MIN_TX_GAP_MS) return;
  last_tx = millis();
#endif
#ifdef BAND_FEATHER
  if (burst) Serial1.print("#BURST\n");  // the Feather sends the next packet on slot, +1 and -1
  Serial1.write((uint8_t)0x20);
  Serial1.write(p, rescue::PACKET_LEN);
  return;
#endif
#ifdef BAND_HOP
  if (burst) {  // ALIVE: the dongle may be one dwell off, so also send on the next and previous slot
    uint64_t slot = hop_slot(now_ms());
    const int64_t offs[3] = {0, 1, -1};
    for (int i = 0; i < 3; i++) {
      radio.setFrequency(hop_freq_mhz(sched.channel(slot + offs[i])));
      radio.transmit(const_cast<uint8_t*>(p), rescue::PACKET_LEN);
    }
    tune(sched.channel(slot));
    return;
  }
#endif
#ifndef BAND_FEATHER
  radio.transmit(const_cast<uint8_t*>(p), rescue::PACKET_LEN);
  radio.startReceive();
#endif
}

void send(const uint8_t p[rescue::PACKET_LEN], Medium m, bool burst = false) {
  if (m == AIR) air_packet(p, burst); else usb_packet(p);
}

void out(int pin, bool on, bool low_on = false) {
  if (pin != NO_PIN) digitalWrite(pin, on != low_on ? HIGH : LOW);
}
void led(bool on) {
  out(PIN_LED, on, LOW_ON_LED);
  out(PIN_BUTTON_LED, on);
#ifdef BAND_FEATHER
  if (feather_led_ready) {
    static bool last = true;
    if (last != on) { Serial1.print(on ? "#L1\n" : "#L0\n"); last = on; }
  }
#endif
}

// The Grove Buzzer v1.3 needs a tone: a steady HIGH only clicks, or beeps unreliably.
void buzz(bool on) {
  if (PIN_BUZZER == NO_PIN) return;
  if (on) tone(PIN_BUZZER, BUZZER_HZ);
  else { noTone(PIN_BUZZER); digitalWrite(PIN_BUZZER, LOW); }
}

void chirp(int ms) {
  buzz(true);
  delay(ms);
  buzz(false);
}

// Blink code on the LED and the vibration motor: SHORT 150 ms, LONG 450 ms, 250 ms gaps.
// Without a motor, the buzzer plays the code instead so the wearer still gets it.
void blink(const uint8_t bits[3]) {
  bool buzz_code = PIN_MOTOR == NO_PIN;
  for (int i = 0; i < 3; i++) {
    led(true);
    out(PIN_MOTOR, true);
    if (buzz_code) buzz(true);
    delay(bits[i] ? 450 : 150);
    led(false);
    out(PIN_MOTOR, false);
    if (buzz_code) buzz(false);
    delay(250);
  }
}

void handle(const uint8_t* raw, Medium m) {
  if (!clock_set()) Out.println("#WARN clock not set, send #T <unix>");
  uint8_t reply[rescue::PACKET_LEN], bits[3];
  bool accepted = false;
  if (raw[0] == rescue::BEACON || raw[0] == rescue::BEACON_ACK) {
    if (!periodic_band || !clock_set()) return;
    auto event = periodic_band->on_packet(raw, rescue::PACKET_LEN, now_slot(), millis(), reply, bits);
    if (event == rescue::PeriodicBand::ACKNOWLEDGED) { Out.println("#CONFIRMED led=5s"); return; }
    if (event == rescue::PeriodicBand::RETRY_CONFIRMATION) { send(reply, m); return; }
    accepted = event == rescue::PeriodicBand::PROMPT;
  } else {
    accepted = band.on_packet(raw, rescue::PACKET_LEN, now_slot(), reply, bits);
  }
  if (accepted) {
    last_was_periodic = raw[0] == rescue::BEACON;
    last_medium = m;
    send(reply, m);  // reply first, then the human cue
    if (last_was_periodic) {
      checkin_button.arm(digitalRead(PIN_BUTTON) == button_pressed_level);
      cue_active = true; cue_started = millis();
      out(PIN_MOTOR, true); buzz(true);
      Out.println("#PROMPT click-click-hold2s-release");
      return;
    }
    Out.printf("#BUZZ %d %d %d\n", bits[0], bits[1], bits[2]);
    if (PIN_MOTOR != NO_PIN) {  // buzzer = "friendly drone here", motor carries the code
      chirp(120);
      delay(200);
    }
    blink(bits);
  } else {
    Out.println("#SILENT");
  }
}

void press(bool physical = false) {
  if (last_was_periodic && !physical) { Out.println("#PRESS use physical gesture"); return; }
  uint8_t alive[rescue::PACKET_LEN];
  bool ready = last_was_periodic ? periodic_band && periodic_band->press(millis(), alive) : band.press(alive);
  if (ready) {
    send(alive, last_medium, true);
    Out.println(last_was_periodic ? "#PRESS awaiting-dog-ack" : "#PRESS");
    if (last_was_periodic) return;
    out(PIN_MOTOR, true);  // feel the press go out
    led(true);
    delay(60);
    out(PIN_MOTOR, false);
    led(false);
  } else {
    Out.println("#PRESS none");
  }
}

#ifdef BAND_FEATHER
// Read what the Feather sent: packets from the air go through the same handle() as always.
// "#HELLO feather radio=ok" or "#OK feather radio=ok ..." mean its radio is up.
void feather_poll() {
  while (Serial1.available()) {
    auto ev = feather_parser.feed((uint8_t)Serial1.read());
    if (ev == rescue::FrameParser::PACKET) {
      handle(feather_parser.packet(), AIR);
    } else if (ev == rescue::FrameParser::LINE) {
      const char* l = feather_parser.line();
      if (strstr(l, "feather radio=ok")) radio_ok = true;
      else if (strstr(l, "feather radio=missing")) radio_ok = false;
      if (l[1] == 'O') Out.printf("%s\n", l);  // the "#OK feather ..." status answer
    }
  }
}
#endif

int start_radio();
extern int radio_code;

#ifndef BAND_FEATHER
uint8_t reg(uint8_t addr) {  // read one radio register directly (debug only)
  SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
  digitalWrite(PIN_LORA_CS, LOW);
  SPI.transfer(addr & 0x7F);
  uint8_t v = SPI.transfer(0x00);
  digitalWrite(PIN_LORA_CS, HIGH);
  SPI.endTransaction();
  return v;
}
#endif

extern int button_pressed_level;
extern bool was_down;

void command(const char* line) {
  if (line[1] == 'T') {
    double t = atof(line + 2);  // seconds, with milliseconds: the hop schedule needs them
    struct timeval tv = {(time_t)t, (suseconds_t)((t - (double)(time_t)t) * 1e6)};
    settimeofday(&tv, nullptr);
#ifdef BAND_FEATHER
    feather_clock();
#endif
    Out.printf("#TIME %u\n", (unsigned)now_slot());
  } else if (line[1] == 'P') {
    press();
  } else if (line[1] == 'L') {
    // Hold each logical LED state so module polarity can be observed by eye.
    Out.println("#LED_TEST OFF 3s");
    led(false); Serial.flush(); delay(3000);
    Out.println("#LED_TEST ON 3s");
    led(true); Serial.flush(); delay(3000);
    Out.println("#LED_TEST OFF 3s");
    led(false); Serial.flush(); delay(3000);
    Out.println("#TEST led done");
  } else if (line[1] == 'E') {
    if (PIN_BUTTON_LED == NO_PIN) {
      Out.println("#LED_TEST disabled: no button LED pin configured");
      return;
    }
    // Bench test: isolate the module LED from gesture/ACK logic for 10 seconds.
    pinMode(PIN_BUTTON_LED, OUTPUT);
    digitalWrite(PIN_BUTTON_LED, LOW);
    Out.printf("#LED_TEST pin=%d LOW 5s button_raw=%d\n", PIN_BUTTON_LED, digitalRead(PIN_BUTTON));
    Serial.flush();
    delay(5000);
    digitalWrite(PIN_BUTTON_LED, HIGH);
    Out.printf("#LED_TEST pin=%d HIGH 5s button_raw=%d\n", PIN_BUTTON_LED, digitalRead(PIN_BUTTON));
    Serial.flush();
    delay(5000);
    led(false);
    Out.println("#LED_TEST done");
  } else if (line[1] == 'B') {
    chirp(300);
    Out.printf("#TEST buzzer done (pin %d)\n", PIN_BUZZER);
  } else if (line[1] == 'Z') {  // buzzer driven with a 2.3 kHz tone (works for passive and active buzzers)
    chirp(300);
    Out.println("#TEST buzzer tone done");
  } else if (line[1] == 'M') {
    if (PIN_MOTOR == NO_PIN) {
      Out.println("#TEST motor off: set PIN_MOTOR once the transistor is in");
    } else {
      int cap = -1;
#ifdef MOTOR_DIRECT_DRIVE
      cap = MOTOR_DIRECT_DRIVE;
      if (line[2] >= '0' && line[2] <= '2') cap = line[2] - '0';  // "#M1": try drive level 1, never 3
      gpio_set_drive_capability((gpio_num_t)PIN_MOTOR, (gpio_drive_cap_t)cap);
#endif
      out(PIN_MOTOR, true); delay(300); out(PIN_MOTOR, false);
      Out.printf("#TEST motor done (drive level %d)\n", cap);
    }
  } else if (line[1] == 'C') {
    const uint8_t bits[3] = {1, 0, 1};
    blink(bits);
    Out.println("#TEST code 101 done");
  } else if (line[1] == 'K') {  // button wiring probe: level with pull-up, then pull-down
    int up = 0, down = 0;
    pinMode(PIN_BUTTON, INPUT_PULLUP); delay(5);
    for (int i = 0; i < 20; i++) { up += digitalRead(PIN_BUTTON); delay(1); }
    pinMode(PIN_BUTTON, INPUT_PULLDOWN); delay(5);
    for (int i = 0; i < 20; i++) { down += digitalRead(PIN_BUTTON); delay(1); }
    pinMode(PIN_BUTTON, BUTTON_MODULE ? INPUT : INPUT_PULLUP);
    was_down = digitalRead(PIN_BUTTON) == button_pressed_level;
    Out.printf("#TEST button pullup=%d/20 high, pulldown=%d/20 high\n", up, down);
  } else if (line[1] == 'J') {  // listen-only: D1 and D3 as plain inputs for 10 s, print changes
    if (PIN_BUTTON_LED != NO_PIN) pinMode(PIN_BUTTON_LED, INPUT);
    pinMode(PIN_BUTTON, INPUT);
    int a = -1, b = -1;
    for (uint32_t t0 = millis(); millis() - t0 < 10000;) {
      int na = PIN_BUTTON_LED != NO_PIN ? digitalRead(PIN_BUTTON_LED) : -1, nb = digitalRead(PIN_BUTTON);
      if (na != a || nb != b) {
        Out.printf("#TEST listen t=%lu D1(led pin)=%d D3(button pin)=%d\n", (unsigned long)(millis() - t0), na, nb);
        a = na; b = nb;
      }
      delay(20);
    }
    if (PIN_BUTTON_LED != NO_PIN) { pinMode(PIN_BUTTON_LED, OUTPUT); digitalWrite(PIN_BUTTON_LED, LOW); }
    pinMode(PIN_BUTTON, BUTTON_MODULE ? INPUT : INPUT_PULLUP);
    was_down = digitalRead(PIN_BUTTON) == button_pressed_level;
    Out.println("#TEST listen done");
#ifndef BAND_FEATHER
  } else if (line[1] == 'V') {  // raw SPI probe: RegVersion must read 0x24 (RFM69) or 0x12 (RFM9x)
    struct Try { const char* name; int sck, miso, mosi; } tries[] = {
        {"as wired", PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI},
        {"MISO/MOSI swapped", PIN_LORA_SCK, PIN_LORA_MOSI, PIN_LORA_MISO}};
    pinMode(PIN_LORA_RST, OUTPUT);  // reset pulse: low 1 ms, then release
    digitalWrite(PIN_LORA_RST, LOW); delay(2);
    pinMode(PIN_LORA_RST, INPUT); delay(10);
    pinMode(PIN_LORA_CS, OUTPUT);
    for (auto& t : tries) {
      SPI.end();
      SPI.begin(t.sck, t.miso, t.mosi, -1);
      uint8_t v[3];
      for (int i = 0; i < 3; i++) {
        digitalWrite(PIN_LORA_CS, LOW);
        SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
        SPI.transfer(REG_VERSION);
        v[i] = SPI.transfer(0x00);
        SPI.endTransaction();
        digitalWrite(PIN_LORA_CS, HIGH);
        delay(2);
      }
      Out.printf("#TEST spi %s: version 0x%02X 0x%02X 0x%02X%s\n", t.name, v[0], v[1], v[2],
                    v[0] == REG_VERSION_OK ? "  <- radio found" : "");
    }
    SPI.end();
    SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_CS);
    Out.printf("#TEST pins cs=%d rst=%d level_rst=%d\n", PIN_LORA_CS, PIN_LORA_RST, digitalRead(PIN_LORA_RST));
#endif
  } else if (line[1] == 'R') {
#ifdef BAND_FEATHER
    Serial1.print("#?\n");  // the Feather answers "#OK feather radio=.. clock=.. ch=..", shown below
    Out.printf("#TEST feather asked; radio=%s (needs the Feather on Serial1: TX/RX crossed, common GND)\n", radio_ok ? "ok" : "missing");
#else
    if (!radio_ok) start_radio();
    Out.printf("#TEST radio begin code %d (-2 = chip not found: SPI wires, CS, RST or 3V3)\n", radio_code);
    Out.printf("#TEST regs version=0x%02X opmode=0x%02X rssi_raw=%d\n", reg(REG_VERSION), reg(0x01), reg(REG_RSSI));
    Out.printf("#TEST radio=%s rssi_now=%.0f\n", radio_ok ? "ok" : "missing", radio_ok ? radio.getRSSI() : 0.0);
#endif
#ifdef BAND_HOP
    Out.printf("#TEST hop channel=%d (%.2f MHz) clock=%s\n", cur_ch, hop_freq_mhz(cur_ch), clock_set() ? "set" : "NOT set");
#endif
#ifdef BAND_FEATHER
  } else if (line[1] == 'Q') {  // probe D7 (Feather TX): a connected, grounded Feather holds it HIGH when idle
    Serial1.end();
    int down = 0, up = 0;
    pinMode(PIN_FEATHER_RX, INPUT_PULLDOWN); delay(5);
    for (int i = 0; i < 20; i++) { down += digitalRead(PIN_FEATHER_RX); delay(1); }
    pinMode(PIN_FEATHER_RX, INPUT_PULLUP); delay(5);
    for (int i = 0; i < 20; i++) { up += digitalRead(PIN_FEATHER_RX); delay(1); }
    Serial1.begin(FEATHER_LINK_BAUD, SERIAL_8N1, PIN_FEATHER_RX, PIN_FEATHER_TX);
    Out.printf("#TEST D7 (Feather TX) with pull-down=%d/20 high, pull-up=%d/20 high\n", down, up);
  } else if (line[1] == 'S') {  // sniff the Feather link: ask "#?", dump the raw bytes that come back
    while (Serial1.available()) Serial1.read();
    Serial1.print("#?\n");
    uint8_t got[48];
    int n = 0, total = 0;
    for (uint32_t t0 = millis(); millis() - t0 < 1500;) {
      while (Serial1.available()) {
        int c = Serial1.read();
        total++;
        if (n < 48) got[n++] = (uint8_t)c;
      }
    }
    Out.printf("#TEST serial1 rx bytes=%d (0 = nothing arrives: power, wires, ground):", total);
    for (int i = 0; i < n; i++) Out.printf(" %02X", got[i]);
    Out.println();
#endif
  } else if (line[1] == 'I') {
    Out.printf("#PAIR_ID %08lX%08lX paired=%d\n", (unsigned long)(pairing_id >> 32),
               (unsigned long)pairing_id, periodic_band ? 1 : 0);
  } else if (line[1] == '?') {
    Out.printf("#OK band radio=%s led_pin=%d button_raw=%d pressed_level=%d\n",
               radio_ok ? "ok" : "missing", PIN_BUTTON_LED, digitalRead(PIN_BUTTON), button_pressed_level);
  }
}

int radio_code = 0;
int start_radio() {
#if defined(BAND_FEATHER)
  Serial1.print("#?\n");  // radio_ok turns true when the Feather answers (feather_poll)
  return radio_code;
#elif defined(RADIO_RFM69)
  radio_code = radio.begin(FSK_FREQ_MHZ, FSK_BITRATE_KBPS, FSK_DEV_KHZ, FSK_RXBW_KHZ, FSK_POWER_DBM, FSK_PREAMBLE_BITS);
  if (radio_code == RADIOLIB_ERR_NONE) {
    // begin() leaves the RFM69HCW on the PA0 output, which the HCW does not wire to the antenna.
    radio_code = radio.setOutputPower(FSK_POWER_DBM, RFM69_HIGH_POWER);
  }
  if (radio_code == RADIOLIB_ERR_NONE) {
    uint8_t sync[] = FSK_SYNC_WORD;
    radio_code = radio.setSyncWord(sync, sizeof sync);
  }
  if (radio_code == RADIOLIB_ERR_NONE) radio_code = radio.setDataShaping(FSK_SHAPING);
  if (radio_code == RADIOLIB_ERR_NONE) radio_code = radio.setCrcFiltering(true);
#else
  radio_code = radio.begin(LORA_FREQ_MHZ, LORA_BW_KHZ, LORA_SF, LORA_CR, RADIOLIB_SX127X_SYNC_WORD, LORA_POWER_DBM);
#endif
#ifndef BAND_FEATHER
  radio_ok = radio_code == RADIOLIB_ERR_NONE;
  if (radio_ok) {
    radio.setPacketReceivedAction(on_rx);
    radio.startReceive();
#ifdef BAND_HOP
    cur_ch = 0;  // radio starts on channel 0; hop_loop moves it once the clock is set
#endif
  }
#endif
  return radio_code;
}

int button_pressed_level = LOW;
bool was_down = false;  // button state, reset after the probe commands  // learned at boot: the opposite of the idle level

void setup() {
  Serial.begin(115200);
  // Use all 48 factory MAC bits, represented as a 16-digit ID. Never a BLE name.
  pairing_id = ESP.getEfuseMac();
#if defined(PERIODIC_PAIR_KEY_BYTES) && defined(PERIODIC_DOG_ID)
  static const uint8_t pair_key[rescue::KEY_LEN] = PERIODIC_PAIR_KEY_BYTES;
  static rescue::PeriodicBand paired(pair_key, PERIODIC_DOG_ID, pairing_id);
  periodic_band = &paired;
#endif
  Out.printf("#PAIR_ID %08lX%08lX paired=%d\n", (unsigned long)(pairing_id >> 32),
             (unsigned long)pairing_id, periodic_band ? 1 : 0);
  pinMode(PIN_LED, OUTPUT);
  if (PIN_BUTTON_LED != NO_PIN) pinMode(PIN_BUTTON_LED, OUTPUT);
  led(false);
  if (PIN_BUZZER != NO_PIN) { pinMode(PIN_BUZZER, OUTPUT); buzz(false); }
  if (PIN_MOTOR != NO_PIN) { pinMode(PIN_MOTOR, OUTPUT); out(PIN_MOTOR, false); }
#ifdef MOTOR_DIRECT_DRIVE
  if (PIN_MOTOR != NO_PIN) gpio_set_drive_capability((gpio_num_t)PIN_MOTOR, (gpio_drive_cap_t)MOTOR_DIRECT_DRIVE);
#endif
  pinMode(PIN_BUTTON, BUTTON_MODULE ? INPUT : INPUT_PULLUP);
  delay(20);
  button_pressed_level = digitalRead(PIN_BUTTON) == HIGH ? LOW : HIGH;  // don't hold it at power-on
  checkin_button.begin(digitalRead(PIN_BUTTON) == button_pressed_level, millis());

#ifdef BAND_FEATHER
  Serial1.begin(FEATHER_LINK_BAUD, SERIAL_8N1, PIN_FEATHER_RX, PIN_FEATHER_TX);
  feather_led_ready = true;
  led(false);
#else
  SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_CS);
#endif
  int st = start_radio();
#ifdef BAND_FEATHER
  for (uint32_t t0 = millis(); !radio_ok && millis() - t0 < 800;) feather_poll();  // give it time to answer
#endif
#if BAND_BLE
  ble_in = xQueueCreate(512, 1);
  char name[20];
  uint64_t mac = ESP.getEfuseMac();
  snprintf(name, sizeof name, "Rescue-Band-%02X%02X", (uint8_t)(mac >> 32), (uint8_t)(mac >> 40));
  BLEDevice::init(name);
  // -3 dBm: keeps the worn device's Bluetooth output under 1 mW.
  BLEDevice::setPower(ESP_PWR_LVL_N3, ESP_BLE_PWR_TYPE_DEFAULT);
  BLEDevice::setPower(ESP_PWR_LVL_N3, ESP_BLE_PWR_TYPE_ADV);
  BLEDevice::setPower(ESP_PWR_LVL_N3, ESP_BLE_PWR_TYPE_SCAN);
  BLEServer* srv = BLEDevice::createServer();
  srv->setCallbacks(new BleConn());
  BLEService* svc = srv->createService(NUS_SERVICE);
  ble_tx = svc->createCharacteristic(NUS_TX, BLECharacteristic::PROPERTY_NOTIFY);
  ble_tx->addDescriptor(new BLE2902());
  BLECharacteristic* rx = svc->createCharacteristic(NUS_RX, BLECharacteristic::PROPERTY_WRITE |
                                                                BLECharacteristic::PROPERTY_WRITE_NR);
  rx->setCallbacks(new BleRx());
  svc->start();
  srv->getAdvertising()->addServiceUUID(NUS_SERVICE);
  srv->getAdvertising()->start();
  Out.printf("#BLE %s\n", name);
#endif
  Out.printf("#HELLO band radio=%s (code %d)\n", radio_ok ? "ok" : "missing", st);
  Out.printf("#PINS sck=%d miso=%d mosi=%d cs=%d rst=%d dio0=%d led=%d button=%d(pressed=%s) buzzer=%d motor=%d\n",
                PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_CS, PIN_LORA_RST, PIN_LORA_DIO0, PIN_LED,
                PIN_BUTTON, button_pressed_level == HIGH ? "HIGH" : "LOW", PIN_BUZZER, PIN_MOTOR);
  led(false);  // status LED is off until a button press or verified success
}

void loop() {
#ifdef BAND_FEATHER
  feather_poll();
  if (millis() - feather_clock_at > 5000) feather_clock();
#endif
#ifdef BAND_HOP
  hop_loop(radio_ok);
#endif
  while (Serial.available()) {
    auto ev = parser.feed((uint8_t)Serial.read());
    if (ev == rescue::FrameParser::PACKET) handle(parser.packet(), USB_LINK);
    else if (ev == rescue::FrameParser::LINE) command(parser.line());
  }
#if BAND_BLE
  char ch;                                      // Bluetooth: same commands and packets as USB
  while (xQueueReceive(ble_in, &ch, 0) == pdTRUE) {
    auto ev = ble_parser.feed((uint8_t)ch);
    if (ev == rescue::FrameParser::PACKET) handle(ble_parser.packet(), USB_LINK);
    else if (ev == rescue::FrameParser::LINE) command(ble_parser.line());
  }
  if (ble_connected && !ble_out.empty()) {      // notify in 20-byte chunks (works at any MTU)
    std::string chunk;
    portENTER_CRITICAL(&ble_mux);
    chunk = ble_out.substr(0, 20);
    ble_out.erase(0, chunk.size());
    portEXIT_CRITICAL(&ble_mux);
    ble_tx->setValue((uint8_t*)chunk.data(), chunk.size());
    ble_tx->notify();
  } else if (!ble_connected) {
    portENTER_CRITICAL(&ble_mux);
    ble_out.clear();
    portEXIT_CRITICAL(&ble_mux);
  }
#endif

#ifndef BAND_FEATHER
  if (rx_flag) {
    rx_flag = false;
    uint8_t buf[rescue::PACKET_LEN];
    size_t len = radio.getPacketLength();
    int st = radio.readData(buf, rescue::PACKET_LEN);
    if (st == RADIOLIB_ERR_NONE && len == rescue::PACKET_LEN) handle(buf, AIR);
    radio.startReceive();
  }
#endif

  uint32_t now = millis();
  if (cue_active && (uint32_t)(now - cue_started) >= 120) {
    cue_active = false; out(PIN_MOTOR, false); buzz(false);
  }
  bool enabled = periodic_band && last_was_periodic && periodic_band->waiting(now);
  bool gesture = checkin_button.update(digitalRead(PIN_BUTTON) == button_pressed_level, now, enabled);
  bool down = checkin_button.pressed();
  if (down != was_down) {
    was_down = down;
    Out.println(down ? "#BTN down" : "#BTN up");
    if (down && !last_was_periodic && !periodic_band) press(true);
  }
  if (gesture) press(true);
  if (periodic_band) {
    uint8_t retry[rescue::PACKET_LEN];
    if (periodic_band->retry_confirmation(now, retry)) send(retry, last_medium, true);
    led(periodic_band->status_led(now));
  }
}
