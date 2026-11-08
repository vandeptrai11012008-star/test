#include <stdio.h>
#include <string.h>
#include "nonce_counter.h"
struct Store {
  uint8_t bytes[32]; int budget = -1;
  Store() { memset(bytes, 0xff, sizeof bytes); }
  uint8_t read(uint8_t at) { return bytes[at]; }
  void update(uint8_t at, uint8_t v) {
    if (bytes[at] == v || budget == 0) return;
    if (budget > 0) --budget;
    bytes[at] = v;
  }
};
static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); ++fails; } } while (0)
uint32_t next(rescue::NonceCounter<Store>& counter) {
  uint8_t out[4]; if (!counter.next(out)) return 0;
  return ((uint32_t)out[0]<<24)|((uint32_t)out[1]<<16)|((uint32_t)out[2]<<8)|out[3];
}
int main() {
  Store store; rescue::NonceCounter<Store> a(store);
  CHECK(a.begin()); CHECK(next(a) == 1); CHECK(next(a) == 2);
  rescue::NonceCounter<Store> reboot(store); CHECK(reboot.begin());
  CHECK(next(reboot) == 257); // unused counters from previous boot are skipped
  Store baseline; rescue::NonceCounter<Store> initial(baseline);
  CHECK(initial.begin());
  for (uint32_t i=1; i<=256; ++i) CHECK(next(initial) == i);
  // Tear every possible write position of the following journal update.
  for (int cut=0; cut<=16; ++cut) {
    Store torn = baseline; torn.budget = cut;
    rescue::NonceCounter<Store> attempt(torn);
    bool initialized = attempt.begin();
    uint32_t issued = initialized ? next(attempt) : 256;
    torn.budget = -1;
    rescue::NonceCounter<Store> recovered(torn);
    CHECK(recovered.begin()); CHECK(next(recovered) > issued);
  }
  Store corrupt; corrupt.bytes[0] = 0;
  rescue::NonceCounter<Store> blocked(corrupt);
  CHECK(!blocked.begin()); CHECK(next(blocked) == 0);
  // A failed reservation cannot return a counter; counter zero is never issued.
  Store failure; failure.budget = 1;
  rescue::NonceCounter<Store> stopped(failure);
  CHECK(!stopped.begin()); CHECK(next(stopped) == 0);
  puts(fails ? "nonce counter: FAILED" : "nonce counter: ok");
  return fails ? 1 : 0;
}
