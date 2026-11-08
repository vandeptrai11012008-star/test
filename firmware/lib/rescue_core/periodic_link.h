#pragma once
#include <string.h>
#include "rescue_core.h"
#include "aes_ccm.h"

namespace rescue {
constexpr uint8_t BEACON = 6, BEACON_REPLY = 7, BEACON_ALIVE = 8, BEACON_ACK = 9;
constexpr uint32_t CHECKIN_COOLDOWN_MS = 7200000UL;
constexpr uint32_t CHECKIN_RESPONSE_MS = 30000UL;

inline void put_id(uint64_t id, uint8_t* out) {
  for (uint8_t i = 0; i < 8; ++i) out[i] = (uint8_t)(id >> (56 - 8 * i));
}
inline uint64_t get_id(const uint8_t* raw) {
  uint64_t id = 0;
  for (uint8_t i = 0; i < 8; ++i) id = (id << 8) | raw[i];
  return id;
}

// Periodic wire v2: clear authenticated TYPE|VER|COUNTER32|SLOT16 (8),
// CCM tag (8), encrypted DOG_ID64|BAND_ID64 (16). Legacy v1 is rejected here.
constexpr uint8_t PERIODIC_VERSION = 2;
inline void periodic_ccm_key(const uint8_t master[KEY_LEN], uint64_t dog, uint64_t band, uint8_t key[16]) {
  const uint8_t label[] = "RESCUE-CCM-v2";
  uint8_t context[sizeof label - 1 + 16], full[32];
  memcpy(context, label, sizeof label - 1);
  put_id(dog, context + sizeof label - 1); put_id(band, context + sizeof label - 1 + 8);
  hmac_sha256(master, KEY_LEN, context, sizeof context, full);
  memcpy(key, full, 16);
  volatile uint8_t* p = full; for (uint8_t i = 0; i < 32; ++i) p[i] = 0;
}
inline void periodic_ccm_nonce(uint8_t type, const uint8_t counter[NONCE_LEN], uint32_t slot, uint8_t nonce[13]) {
  nonce[0] = type; memcpy(nonce + 1, counter, NONCE_LEN);
  for (uint8_t i = 0; i < 4; ++i) nonce[5 + i] = (uint8_t)(slot >> (24 - 8 * i));
  nonce[9] = 'R'; nonce[10] = 'B'; nonce[11] = 2; nonce[12] = 0;
}
inline void make_beacon(const uint8_t key[KEY_LEN], uint8_t type, const uint8_t counter[NONCE_LEN],
                        uint32_t slot, uint64_t dog, uint64_t band, uint8_t out[PACKET_LEN]) {
  uint8_t plain[16], encryption_key[16], nonce[13];
  memset(out, 0, PACKET_LEN);
  out[0] = type; out[1] = PERIODIC_VERSION; memcpy(out + 2, counter, NONCE_LEN);
  out[6] = slot >> 8; out[7] = slot;
  put_id(dog, plain); put_id(band, plain + 8);
  periodic_ccm_key(key, dog, band, encryption_key);
  periodic_ccm_nonce(type, counter, slot, nonce);
  if (!ccm_seal(encryption_key, nonce, out, 8, plain, 16, out + 16, out + 8)) memset(out, 0, PACKET_LEN);
  volatile uint8_t* p = encryption_key; for (uint8_t i = 0; i < 16; ++i) p[i] = 0;
}
inline bool verify_beacon(const uint8_t key[KEY_LEN], const uint8_t* raw, size_t len,
                          uint8_t type, uint32_t now_slot, uint64_t dog, uint64_t band,
                          uint32_t* matched = nullptr) {
  if (!dog || !band || len != PACKET_LEN || raw[0] != type || raw[1] != PERIODIC_VERSION) return false;
  const int8_t offsets[] = {0, -1, 1};
  uint16_t slot16 = ((uint16_t)raw[6] << 8) | raw[7];
  uint8_t encryption_key[16], expected_ids[16], plain[16], nonce[13];
  periodic_ccm_key(key, dog, band, encryption_key);
  put_id(dog, expected_ids); put_id(band, expected_ids + 8);
  bool valid = false;
  for (int8_t off : offsets) {
    uint32_t slot = now_slot + off;
    if ((slot & 0xffff) != slot16) continue;
    periodic_ccm_nonce(type, raw + 2, slot, nonce);
    if (ccm_open(encryption_key, nonce, raw, 8, raw + 16, 16, raw + 8, plain) && ct_equal(plain, expected_ids, 16)) {
      if (matched) *matched = slot;
      valid = true;
      break;
    }
  }
  volatile uint8_t* p = encryption_key; for (uint8_t i = 0; i < 16; ++i) p[i] = 0;
  return valid;
}

class PeriodicBand {
 public:
  enum Event { SILENT, PROMPT, RETRY_CONFIRMATION, ACKNOWLEDGED };
  PeriodicBand(const uint8_t key[KEY_LEN], uint64_t dog, uint64_t band) : dog_(dog), band_(band) {
    memcpy(key_, key, KEY_LEN);
  }
  bool cooling(uint32_t now) const {
    return confirmed_ && (uint32_t)(now - confirmed_at_) < CHECKIN_COOLDOWN_MS;
  }
  bool waiting(uint32_t now) const {
    return pending_ && (uint32_t)(now - began_) < CHECKIN_RESPONSE_MS && !cooling(now);
  }
  bool success_led(uint32_t now) const { return acknowledged_ && (uint32_t)(now - acknowledged_at_) < 5000; }
  bool status_led(uint32_t now) const {
    return waiting(now) ||
           (awaiting_ack_ && (uint32_t)(now - confirmed_at_) < CHECKIN_RESPONSE_MS) ||
           success_led(now);
  }
  // Local bench reset only. Retain identities, pair key and replay cache.
  void clear_wait() {
    pending_ = confirmed_ = awaiting_ack_ = acknowledged_ = false;
  }
  Event on_packet(const uint8_t* raw, size_t len, uint32_t slot, uint32_t now,
                 uint8_t reply[PACKET_LEN], uint8_t bits[3]) {
    uint32_t matched;
    if (len == PACKET_LEN && raw[0] == BEACON_ACK) {
      if (!awaiting_ack_ || (uint32_t)(now - confirmed_at_) >= CHECKIN_RESPONSE_MS ||
          memcmp(nonce_, raw + 2, NONCE_LEN) ||
          !verify_beacon(key_, raw, len, BEACON_ACK, slot, dog_, band_, &matched) || matched != slot_) return SILENT;
      awaiting_ack_ = false; acknowledged_ = true; acknowledged_at_ = now;
      confirmed_ = true; confirmed_at_ = now;
      return ACKNOWLEDGED;
    }
    if (!verify_beacon(key_, raw, len, BEACON, slot, dog_, band_, &matched)) return SILENT;
    if (cooling(now) || (awaiting_ack_ && (uint32_t)(now - confirmed_at_) < CHECKIN_RESPONSE_MS)) {
      if (memcmp(nonce_, raw + 2, NONCE_LEN) || slot_ != matched) return SILENT;
      make_beacon(key_, BEACON_ALIVE, nonce_, slot_, dog_, band_, reply);
      return RETRY_CONFIRMATION; // recover a lost button packet without another vibration
    }
    for (uint8_t i = 0; i < count_; ++i)
      if (!memcmp(seen_[i], raw + 2, NONCE_LEN)) return SILENT;
    memcpy(seen_[head_], raw + 2, NONCE_LEN);
    head_ = (head_ + 1) % 64;
    if (count_ < 64) ++count_;
    memcpy(nonce_, raw + 2, NONCE_LEN);
    slot_ = matched; began_ = now; pending_ = true; confirmed_ = false;
    awaiting_ack_ = acknowledged_ = false;
    make_beacon(key_, BEACON_REPLY, nonce_, slot_, dog_, band_, reply);
    blink_bits(key_, slot_, bits);
    return PROMPT;
  }
  bool press(uint32_t now, uint8_t out[PACKET_LEN]) {
    if (!pending_ || (uint32_t)(now - began_) >= CHECKIN_RESPONSE_MS || cooling(now)) return false;
    make_beacon(key_, BEACON_ALIVE, nonce_, slot_, dog_, band_, out);
    pending_ = false; confirmed_ = false; confirmed_at_ = now;
    awaiting_ack_ = true; acknowledged_ = false; last_confirmation_tx_ = now;
    return true;
  }
  bool retry_confirmation(uint32_t now, uint8_t out[PACKET_LEN]) {
    if (!awaiting_ack_ || (uint32_t)(now - confirmed_at_) >= CHECKIN_RESPONSE_MS ||
        (uint32_t)(now - last_confirmation_tx_) < 2000) return false;
    make_beacon(key_, BEACON_ALIVE, nonce_, slot_, dog_, band_, out);
    last_confirmation_tx_ = now;
    return true;
  }
 private:
  uint8_t key_[KEY_LEN], nonce_[NONCE_LEN], seen_[64][NONCE_LEN];
  uint64_t dog_, band_;
  uint32_t slot_ = 0, began_ = 0, confirmed_at_ = 0;
  uint8_t head_ = 0, count_ = 0;
  bool pending_ = false, confirmed_ = false;
  bool awaiting_ack_ = false, acknowledged_ = false;
  uint32_t acknowledged_at_ = 0, last_confirmation_tx_ = 0;
};
}  // namespace rescue
