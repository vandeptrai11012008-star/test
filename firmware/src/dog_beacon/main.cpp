// Feather 32u4 RFM69HCW or RFM9x in FSK mode: periodic discovery with a separate human ALIVE response.
// No robot movement. USB #T sets the shared hopping clock; #A 0 stops broadcasts.
#include <Arduino.h>
#include "config.h"
#include "hop.h"
#include "periodic_dog.h"
#include "beacon_radio.h"

static const uint8_t KEY[rescue::KEY_LEN] = GROUP_KEY_BYTES;
HopSchedule schedule(KEY);
#ifndef PERIODIC_DOG_ID
#define PERIODIC_DOG_ID 0ULL
#endif
struct Peer { uint64_t id; uint8_t key[rescue::KEY_LEN]; };
#ifdef PERIODIC_PEERS
static const Peer peers[] = PERIODIC_PEERS;
static const uint8_t peer_count = sizeof peers / sizeof peers[0];
static_assert(sizeof peers / sizeof peers[0] <= 4, "32u4 build supports at most four paired bands");
#else
static const Peer peers[] = {{0, {0}}};
static const uint8_t peer_count = 0;
#endif
rescue::PeriodicDog* beacons[4] = {};
bool pairing_ok = false;
uint16_t period_seconds = 2;
uint32_t last_tx_ms = 0;
bool have_tx = false, have_confirmation = false;
uint32_t last_confirmation = 0;
uint8_t next_peer = 0;
void print_id(uint64_t id) {
  for (int8_t i = 15; i >= 0; --i) Serial.print((uint8_t)((id >> (i * 4)) & 15), HEX);
}
rescue::FrameParser parser;
bool radio_ok = false, clock_ok = false;
int16_t radio_code = -1;
volatile bool rx_flag = false;
uint64_t base_unix_ms = 0;
uint32_t base_millis = 0;
int channel = -1;

uint64_t now_ms() { return base_unix_ms + (uint32_t)(millis() - base_millis); }
uint32_t now_slot() { return now_ms() / (1000ULL * rescue::SLOT_SECONDS); }
void on_rx() { rx_flag = true; }

void listen(uint8_t ch) {
  radio_tune(ch);
  channel = ch;
  radio_listen();
}

void status() {
  Serial.print(F("#OK dog radio=")); Serial.print(radio_ok ? F("ok") : F("missing"));
  Serial.print(F(" code=")); Serial.print(radio_code);
  Serial.print(F(" clock=")); Serial.print(clock_ok ? F("set") : F("NOT-set"));
  Serial.print(F(" auto=")); Serial.print(period_seconds);
  Serial.print(F(" paired=")); Serial.print(pairing_ok ? peer_count : 0);
  Serial.println(F(" clear_wait=1"));
  for (uint8_t i = 0; pairing_ok && i < peer_count; ++i) {
    uint32_t now = millis();
    Serial.print(F("#PEER band=")); print_id(peers[i].id);
    Serial.print(F(" state="));
    switch (beacons[i]->state(now)) {
      case rescue::PeriodicDog::CONFIRMED: Serial.print(F("CONFIRMED_WAIT_2H")); break;
      case rescue::PeriodicDog::BAND_SEEN: Serial.print(F("BAND_SEEN_WAIT_BUTTON")); break;
      default: Serial.print(F("WAITING"));
    }
    Serial.print(F(" remaining_s=")); Serial.println(beacons[i]->remaining_seconds(now));
  }
}

void command(const char* line) {
  if (line[1] == 'T') {
    const char* p = line + 2;
    while (*p == ' ') ++p;
    uint32_t sec = 0, ms = 0;
    while (*p >= '0' && *p <= '9') sec = sec * 10 + (*p++ - '0');
    if (*p == '.') {
      ++p;
      for (uint8_t i = 0; i < 3; ++i)
        ms = ms * 10 + (*p >= '0' && *p <= '9' ? (*p++ - '0') : 0);
    }
    while (*p == ' ') ++p;
    if (*p || sec <= 1700000000UL) { Serial.println(F("#ERR invalid clock")); return; }
    uint64_t updated = (uint64_t)sec * 1000 + ms;
    uint64_t previous = now_ms();
    // Frequent clock refreshes should not cancel the wearer's pending button press.
    if (!clock_ok || updated > previous + 1000 || previous > updated + 1000)
      for (uint8_t i = 0; pairing_ok && i < peer_count; ++i) beacons[i]->clear();
    base_unix_ms = updated; base_millis = millis(); clock_ok = true;
    Serial.print(F("#TIME ")); Serial.println(now_slot());
  } else if (line[1] == 'A') {
    char* end;
    long seconds = strtol(line + 2, &end, 10);
    bool digits = end != line + 2;
    while (*end == ' ') ++end;
    if (!digits || *end || seconds < 0 || seconds > 3600) { Serial.println(F("#ERR auto 0..3600")); return; }
    period_seconds = (uint16_t)seconds;
    for (uint8_t i = 0; pairing_ok && i < peer_count; ++i) beacons[i]->set_period(period_seconds);
    have_tx = false;
    status();
  } else if (strcmp(line, "#F") == 0) {
    for (uint8_t i = 0; pairing_ok && i < peer_count; ++i) beacons[i]->clear_wait();
    have_confirmation = false; have_tx = false;
    digitalWrite(13, LOW);
    Serial.println(F("#WAIT_CLEARED dog"));
  } else if (line[1] == '?') status();
}

void fresh_nonce(uint8_t out[rescue::NONCE_LEN]) {
  // Prototype entropy, as on the existing AVR dog: no hardware CSPRNG on a 32u4.
  // A keyed pool mixes runtime timing, analog samples and a counter.
  static uint8_t pool[32] = {};
  static uint32_t count = 0;
  uint8_t material[48];
  memcpy(material, pool, 32);
  uint32_t ticks = micros(), sequence = ++count;
  memcpy(material + 32, &ticks, 4); memcpy(material + 36, &sequence, 4);
  for (uint8_t i = 0; i < 8; ++i) material[40 + i] = (uint8_t)analogRead(A0) ^ (uint8_t)micros();
  rescue::hmac_sha256(KEY, sizeof KEY, material, sizeof material, pool);
  memcpy(out, pool, rescue::NONCE_LEN);
}

void setup() {
  Serial.begin(115200);
  pinMode(13, OUTPUT);
  pairing_ok = PERIODIC_DOG_ID != 0 && peer_count > 0;
  for (uint8_t i = 0; i < peer_count; ++i) {
    if (!peers[i].id) pairing_ok = false;
    for (uint8_t j = 0; j < i; ++j) if (peers[i].id == peers[j].id) pairing_ok = false;
  }
  for (uint8_t i = 0; pairing_ok && i < peer_count; ++i)
    beacons[i] = new rescue::PeriodicDog(peers[i].key, PERIODIC_DOG_ID, peers[i].id);
  int st = begin_radio();
  radio_code = st;
  radio_ok = st == RADIO_OK;
  if (radio_ok) radio_listen();
  Serial.print(F("#HELLO dog radio=")); Serial.print(radio_ok ? F("ok") : F("missing"));
  Serial.print(BEACON_RADIO_MODEL); Serial.println(st);
  status();
}

void loop() {
  while (Serial.available()) {
    if (parser.feed((uint8_t)Serial.read()) == rescue::FrameParser::LINE) command(parser.line());
  }
  if (!radio_ok || !clock_ok || !pairing_ok) return;
  if (radio_ready()) {
    rx_flag = false;
    uint8_t raw[rescue::PACKET_LEN];
    int len = radio_read(raw);
    for (uint8_t i = 0; len == (int)rescue::PACKET_LEN && i < peer_count; ++i) {
      uint32_t received_at = millis();
      bool fresh = beacons[i]->receive(raw, len, received_at, now_slot());
      uint8_t ack[rescue::PACKET_LEN];
      bool receipt = beacons[i]->receipt(raw, len, received_at, now_slot(), ack);
      if (!fresh && !receipt) continue;
      if (receipt && radio_send(ack) != RADIO_OK) Serial.println(F("#ERR ack transmit"));
      if (fresh) {
        bool confirmed = beacons[i]->state(received_at) == rescue::PeriodicDog::CONFIRMED;
        if (confirmed) { have_confirmation = true; last_confirmation = received_at; }
        Serial.print(confirmed ? F("#PERSON_CONFIRMED band=") : F("#BAND_SEEN band="));
        print_id(peers[i].id); Serial.println(confirmed ? F(" next=2h") : F(" wait=button"));
      }
      break;
    }
    radio_listen();
  }
  uint32_t now = millis();
  digitalWrite(13, have_confirmation && (uint32_t)(now - last_confirmation) < 5000 ? HIGH : LOW);
  uint64_t hop = hop_slot(now_ms());
  int8_t selected = -1;
  if (period_seconds && (!have_tx || (uint32_t)(now - last_tx_ms) >= (uint32_t)period_seconds * 1000)) {
    for (uint8_t j = 0; j < peer_count; ++j) {
      uint8_t i = (next_peer + j) % peer_count;
      if (beacons[i]->due(now)) { selected = i; next_peer = (i + 1) % peer_count; break; }
    }
  }
  if (selected >= 0) {
    uint8_t nonce[rescue::NONCE_LEN], raw[rescue::PACKET_LEN];
    fresh_nonce(nonce);
    beacons[selected]->challenge(now, now_slot(), nonce, raw);
    last_tx_ms = now; have_tx = true;
    // Same challenge on neighbouring dwell channels accommodates small clock drift.
    const int8_t offsets[] = {0, 1, -1};
    for (uint8_t i = 0; i < 3; ++i) {
      radio_tune(schedule.channel(hop + offsets[i]));
      int st = radio_send(raw);
      if (st != RADIO_OK) Serial.println(F("#ERR transmit"));
    }
    rx_flag = false;
    listen(schedule.channel(hop_slot(now_ms())));
    Serial.print(F("#BEACON band=")); print_id(peers[selected].id); Serial.println(F(" wait=button"));
  } else {
    uint8_t ch = schedule.channel(hop);
    if (ch != channel && !rx_flag) listen(ch);
  }
}
