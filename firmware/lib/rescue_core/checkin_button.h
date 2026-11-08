#pragma once
#include <stdint.h>

namespace rescue {
// Two short click/releases, then a third press held >=2 s and released.
// Debounce is stable-state based. All elapsed checks tolerate millis() rollover.
class CheckinButton {
 public:
  void begin(bool down, uint32_t now) {
    initialized_ = true; raw_ = pressed_ = down; changed_ = now;
    arm(down);
  }
  void arm(bool down) { stage_ = IDLE; release_first_ = down; }
  bool pressed() const { return pressed_; }
  bool update(bool down, uint32_t now, bool enabled) {
    if (!initialized_) begin(down, now);
    if (down != raw_) { raw_ = down; changed_ = now; }
    bool edge = false;
    if (raw_ != pressed_ && (uint32_t)(now - changed_) >= 40) {
      pressed_ = raw_; edge = true;
    }
    if (!enabled) { stage_ = IDLE; return false; }
    if (release_first_) { if (!pressed_ && !raw_) release_first_ = false; return false; }
    if ((stage_ == WAIT_SECOND || stage_ == WAIT_THIRD) && (uint32_t)(now - released_) > 1500)
      stage_ = IDLE;
    if (!edge) return false;
    if (pressed_) {
      started_ = now;
      if (stage_ == WAIT_SECOND) stage_ = SECOND;
      else if (stage_ == WAIT_THIRD) stage_ = HOLD;
      else stage_ = FIRST;
      return false;
    }
    uint32_t held = now - started_;
    released_ = now;
    if (stage_ == FIRST && held <= 500) stage_ = WAIT_SECOND;
    else if (stage_ == SECOND && held <= 500) stage_ = WAIT_THIRD;
    else if (stage_ == HOLD && held >= 2000) { stage_ = IDLE; return true; }
    else stage_ = IDLE;
    return false;
  }
 private:
  enum Stage { IDLE, FIRST, WAIT_SECOND, SECOND, WAIT_THIRD, HOLD } stage_ = IDLE;
  uint32_t changed_ = 0, started_ = 0, released_ = 0;
  bool initialized_ = false, raw_ = false, pressed_ = false, release_first_ = false;
};
} // namespace rescue
