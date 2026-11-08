#pragma once
#include <string.h>
#include "rescue_core.h"

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

// The entire PAD carries DOG_ID(8) | BAND_ID(8), covered by the HMAC.
// Legacy packet types 1..5 remain unchanged. IDs are public; keys are not.
inline void make_beacon(const uint8_t key[KEY_LEN], uint8_t type, const uint8_t nonce[NONCE_LEN],
                        uint32_t slot, uint64_t dog, uint64_t band, uint8_t out[PACKET_LEN]) {
  Packet p = make(key, type, nonce, slot);
  encode(p, out);
  put_id(dog, out + 16); put_id(band, out + 24);
  tag_ext(key, type, nonce, slot, out + 16, 16, out + 8);
}

inline bool verify_beacon(const uint8_t key[KEY_LEN], const uint8_t* raw, size_t len,
                          uint8_t type, uint32_t now_slot, uint64_t dog, uint64_t band,
                          uint32_t* matched = nullptr) {
  if (!dog || !band || len != PACKET_LEN || raw[0] != type || raw[1] != VERSION ||
      get_id(raw + 16) != dog || get_id(raw + 24) != band) return false;
  const int8_t offsets[] = {0, -1, 1};
  uint16_t slot16 = ((uint16_t)raw[6] << 8) | raw[7];
  for (int8_t off : offsets) {
    uint32_t slot = now_slot + off;
    if ((slot & 0xffff) != slot16) continue;
    uint8_t mac[TAG_LEN];
    tag_ext(key, type, raw + 2, slot, raw + 16, 16, mac);
    if (ct_equal(mac, raw + 8, TAG_LEN)) {
      if (matched) *matched = slot;
      return true;
    }
  }
  return false;
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
