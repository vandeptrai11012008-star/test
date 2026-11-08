// Checks the keyed FSK hop schedule (include/hop.h) against the channel-count, spacing and equal-use rules the design relies on.
#define LINK_FSK 1
#include <stdio.h>

#include "hop.h"

static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)

int main() {
  const uint8_t keyA[16] = {0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6, 0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c};
  uint8_t keyB[16]; memcpy(keyB, keyA, 16); keyB[0] ^= 1;

  // channel plan: 50+ channels inside 903-927 MHz, at least 250 kHz apart
  CHECK(FSK_CHANNELS >= 50);
  CHECK(hop_freq_mhz(0) >= 903.0);
  CHECK(hop_freq_mhz(FSK_CHANNELS - 1) <= 927.0);
  for (int i = 1; i < FSK_CHANNELS; i++) CHECK(hop_freq_mhz(i) - hop_freq_mhz(i - 1) >= 0.25 - 1e-6);

  HopSchedule a(keyA), a2(keyA), b(keyB);
  const int CYCLES = 3000;
  static int use[FSK_CHANNELS], first[FSK_CHANNELS];
  int diff_key = 0, diff_cycle = 0;
  uint8_t prev_first_cycle[FSK_CHANNELS] = {0};
  for (int c = 0; c < CYCLES; c++) {
    int seen[FSK_CHANNELS] = {0};
    for (int i = 0; i < FSK_CHANNELS; i++) {
      uint64_t slot = (uint64_t)c * FSK_CHANNELS + i;
      uint8_t ch = a.channel(slot);
      CHECK(ch < FSK_CHANNELS);
      CHECK(ch == a2.channel(slot));                    // both ends compute the same channel
      seen[ch]++; use[ch]++;
      if (i == 0) first[ch]++;
      if (ch != b.channel(slot)) diff_key++;            // another key gives another order
    }
    for (int i = 0; i < FSK_CHANNELS; i++) CHECK(seen[i] == 1);  // every cycle visits each channel once
    if (c > 0 && a.channel((uint64_t)c * FSK_CHANNELS + 1) != prev_first_cycle[1]) diff_cycle++;
    prev_first_cycle[1] = a.channel((uint64_t)c * FSK_CHANNELS + 1);
  }
  for (int i = 0; i < FSK_CHANNELS; i++) {
    CHECK(use[i] == CYCLES);                                           // equal use, exactly
    CHECK(first[i] > CYCLES / FSK_CHANNELS / 2 && first[i] < CYCLES / FSK_CHANNELS * 2);  // not stuck
  }
  CHECK(diff_key > CYCLES * FSK_CHANNELS * 9 / 10);
  CHECK(diff_cycle > CYCLES * 9 / 10);                                 // cycles do not repeat

  // clocks 3 s apart still pick the same slot most of the time; a whole dwell apart picks a neighbour
  CHECK(hop_slot(1700000000123ULL) == hop_slot(1700000000123ULL + 100));
  CHECK(hop_slot(1700000000000ULL + FSK_DWELL_MS) == hop_slot(1700000000000ULL) + 1);
  // out-of-order queries (slot, slot+1, slot-1) must give the same answers as in-order ones
  uint64_t s = 1700000000000ULL / FSK_DWELL_MS;
  uint8_t c0 = a.channel(s), c1 = a.channel(s + 1), cm = a.channel(s - 1);
  HopSchedule fresh(keyA);
  CHECK(fresh.channel(s - 1) == cm && fresh.channel(s) == c0 && fresh.channel(s + 1) == c1);

  puts(fails ? "hop: FAILED" : "hop: ok");
  return fails ? 1 : 0;
}
