#pragma once
#include <stdint.h>

namespace rescue {
// EEPROM bytes 0..31 are owned by this allocator. Reserve 256 counters BEFORE
// issuing any. Two journal slots and a commit byte tolerate interrupted writes.
// Erased storage initializes; corrupt non-erased storage fails closed.
// Never erase/roll back/copy EEPROM while retaining the same pairing keys.
template<class Store>
class NonceCounter {
 public:
  explicit NonceCounter(Store& store) : store_(store) {}
  bool begin() {
    ready_ = false;
    uint32_t first = 0, second = 0;
    bool a = load(0, first), b = load(1, second);
    if (!a && !b) {
      for (uint8_t i = 0; i < 32; ++i) if (store_.read(i) != 0xff) return false;
      high_ = 0; current_ = 1;
    } else if (a && (!b || first >= second)) { high_ = first; current_ = 0; }
    else { high_ = second; current_ = 1; }
    return reserve();
  }
  bool next(uint8_t out[4]) {
    if (!ready_) return false;
    if (next_ > high_ && !reserve()) return false;
    uint32_t value = next_++;
    for (uint8_t i = 0; i < 4; ++i) out[i] = (uint8_t)(value >> (24 - 8 * i));
    return true;
  }
 private:
  Store& store_;
  uint32_t high_ = 0, next_ = 0;
  uint8_t current_ = 1;
  bool ready_ = false;
  uint32_t read32(uint8_t addr) {
    uint32_t v = 0; for (uint8_t i = 0; i < 4; ++i) v = (v << 8) | store_.read(addr + i); return v;
  }
  bool load(uint8_t slot, uint32_t& high) {
    uint8_t p = slot * 16;
    if (store_.read(p + 15) != 0xa5 || read32(p) != 0x52424332UL) return false;
    high = read32(p + 4);
    return high > 0 && high % 256 == 0 && read32(p + 8) == ~high;
  }
  void write32(uint8_t addr, uint32_t value) {
    for (uint8_t i = 0; i < 4; ++i) store_.update(addr + i, (uint8_t)(value >> (24 - 8 * i)));
  }
  bool reserve() {
    if (high_ > 0xffffffffUL - 256) { ready_ = false; return false; }
    uint32_t reserved = high_ + 256;
    uint8_t target = current_ ^ 1, p = target * 16;
    store_.update(p + 15, 0); // invalidate target; the other committed record survives
    write32(p, 0x52424332UL); write32(p + 4, reserved); write32(p + 8, ~reserved);
    store_.update(p + 15, 0xa5); // commit last
    uint32_t check;
    if (!load(target, check) || check != reserved) { ready_ = false; return false; }
    next_ = high_ + 1; high_ = reserved; current_ = target; ready_ = true;
    return true;
  }
};
}
