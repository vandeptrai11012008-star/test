// Keyed hop schedule for the FSK link (frequency hopping across the 902-928 MHz band).
// 50 channels, 480 kHz apart, 903.10 .. 926.62 MHz. Time is cut into dwell slots of FSK_DWELL_MS;
// each run of 50 slots visits every channel exactly once, in an order derived from the group key
// and the cycle number (HMAC-SHA256), so the order is pseudorandom, equal-use, and identical on
// the band and the dongle. Both ends compute the channel from their own wall clock, so the
// receiver shifts frequency in step with the transmitter. Needs the 20 dB bandwidth of one
// channel under 250 kHz (a measurement, not verified here).
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "config.h"
#include "rescue_core.h"

#ifdef FSK_HOP
inline float hop_freq_mhz(uint8_t ch) { return FSK_CH0_MHZ + FSK_CH_STEP_MHZ * ch; }
inline uint64_t hop_slot(uint64_t unix_ms) { return unix_ms / FSK_DWELL_MS; }

class HopSchedule {
 public:
  explicit HopSchedule(const uint8_t key[rescue::KEY_LEN]) { memcpy(key_, key, sizeof key_); }

  // Channel for a dwell slot. Slots 50k .. 50k+49 are one cycle: a permutation of all channels.
  uint8_t channel(uint64_t slot) {
    uint64_t cycle = slot / FSK_CHANNELS;
    if (!have_ || cycle != cycle_) { build(cycle); cycle_ = cycle; have_ = true; }
    return order_[slot % FSK_CHANNELS];
  }

 private:
  uint8_t key_[rescue::KEY_LEN];
  uint8_t order_[FSK_CHANNELS];
  uint64_t cycle_ = 0;
  bool have_ = false;
  uint8_t block_[32];
  int used_ = 32;
  uint8_t ctr_ = 0;
  uint8_t msg_[12];

  uint16_t next16() {  // counter-mode HMAC byte stream
    if (used_ + 2 > 32) {
      msg_[11] = ctr_++;
      rescue::hmac_sha256(key_, sizeof key_, msg_, sizeof msg_, block_);
      used_ = 0;
    }
    uint16_t v = (uint16_t)((block_[used_] << 8) | block_[used_ + 1]);
    used_ += 2;
    return v;
  }

  void build(uint64_t cycle) {
    msg_[0] = 'h'; msg_[1] = 'o'; msg_[2] = 'p';
    for (int i = 0; i < 8; i++) msg_[3 + i] = (uint8_t)(cycle >> (56 - 8 * i));
    used_ = 32;
    ctr_ = 0;
    for (int i = 0; i < FSK_CHANNELS; i++) order_[i] = (uint8_t)i;
    for (int i = FSK_CHANNELS - 1; i > 0; i--) {  // Fisher-Yates, rejection sampling: no modulo bias
      uint32_t range = (uint32_t)i + 1, limit = (65536u / range) * range;
      uint16_t v;
      do { v = next16(); } while (v >= limit);
      int j = v % range;
      uint8_t t = order_[i]; order_[i] = order_[j]; order_[j] = t;
    }
  }
};
#endif
