#pragma once

#include <string.h>
#include "periodic_link.h"

namespace rescue {

// One human-response window spans many broadcasts. Reusing its challenge prevents
// the band from buzzing on every retry and keeps the button's nonce outstanding.
class PeriodicDog {
 public:
  enum State { WAITING, BAND_SEEN, CONFIRMED };
  static constexpr uint32_t RESPONSE_MS = CHECKIN_RESPONSE_MS;

  PeriodicDog(const uint8_t key[KEY_LEN], uint64_t dog, uint64_t band) : dog_(dog), band_(band) {
    memcpy(key_, key, KEY_LEN);
  }

  bool set_period(uint16_t seconds) {
    if (seconds > 3600) return false;
    period_ms_ = (uint32_t)seconds * 1000;
    clear();
    return true;
  }
  void clear() { active_ = sent_ = false; state_ = WAITING; }
  void clear_wait() { confirmed_ = false; confirmed_at_ = 0; clear(); }
  uint16_t period() const { return period_ms_ / 1000; }
  uint64_t band_id() const { return band_; }
  bool cooling(uint32_t now) const {
    return confirmed_ && (uint32_t)(now - confirmed_at_) < CHECKIN_COOLDOWN_MS;
  }
  uint32_t remaining_seconds(uint32_t now) const {
    if (!cooling(now)) return 0;
    return (CHECKIN_COOLDOWN_MS - (uint32_t)(now - confirmed_at_) + 999) / 1000;
  }
  State state(uint32_t now) const { return cooling(now) ? CONFIRMED : active(now) ? state_ : WAITING; }
  bool active(uint32_t now) const { return active_ && (uint32_t)(now - began_) < RESPONSE_MS; }
  bool due(uint32_t now) const {
    if (!period_ms_ || !dog_ || !band_ || cooling(now)) return false;
    if (!active(now)) return !sent_ || (uint32_t)(now - sent_at_) >= period_ms_;
    return state_ != CONFIRMED && (!sent_ || (uint32_t)(now - sent_at_) >= period_ms_);
  }

  bool challenge(uint32_t now, uint32_t slot, const uint8_t nonce[NONCE_LEN], uint8_t out[PACKET_LEN]) {
    if (!due(now)) return false;
    if (!active(now)) {
      memcpy(nonce_, nonce, NONCE_LEN);
      slot_ = slot;
      began_ = now;
      active_ = true;
      confirmed_ = false;
      state_ = WAITING;
    }
    make_beacon(key_, BEACON, nonce_, slot_, dog_, band_, out);
    sent_ = true;
    sent_at_ = now;
    return true;
  }

  // REPLY means the radio answered; only ALIVE means the wearer pressed the button.
  bool receive(const uint8_t* raw, size_t len, uint32_t now, uint32_t slot) {
    if (!period_ms_ || !active(now) || cooling(now) || len != PACKET_LEN) return false;
    uint32_t matched;
    if ((raw[0] != BEACON_ALIVE && raw[0] != BEACON_REPLY) || memcmp(raw + 2, nonce_, NONCE_LEN) != 0 ||
        !verify_beacon(key_, raw, len, raw[0], slot, dog_, band_, &matched) || matched != slot_) return false;
    if (raw[0] == BEACON_ALIVE) {
      state_ = CONFIRMED; confirmed_ = true; confirmed_at_ = now; active_ = false;
      return true;
    }
    if (raw[0] == BEACON_REPLY && state_ == WAITING) { state_ = BAND_SEEN; return true; }
    return false;
  }

  // ACK duplicates for 30 s to recover lost receipts, without extending cooldown.
  bool receipt(const uint8_t* raw, size_t len, uint32_t now, uint32_t slot, uint8_t out[PACKET_LEN]) const {
    uint32_t matched;
    if (!cooling(now) || (uint32_t)(now - confirmed_at_) >= RESPONSE_MS || len != PACKET_LEN ||
        memcmp(raw + 2, nonce_, NONCE_LEN) ||
        !verify_beacon(key_, raw, len, BEACON_ALIVE, slot, dog_, band_, &matched) || matched != slot_) return false;
    make_beacon(key_, BEACON_ACK, nonce_, slot_, dog_, band_, out);
    return true;
  }

 private:
  uint8_t key_[KEY_LEN], nonce_[NONCE_LEN];
  uint64_t dog_, band_;
  uint32_t period_ms_ = 2000, began_ = 0, sent_at_ = 0, slot_ = 0;
  bool active_ = false, sent_ = false;
  bool confirmed_ = false;
  uint32_t confirmed_at_ = 0;
  State state_ = WAITING;
};

}  // namespace rescue
