// Prints the hop channel for a run of slots (demo key), so the Python schedule can be compared with it.
#define LINK_FSK 1
#include <stdio.h>
#include <stdlib.h>

#include "hop.h"

int main(int argc, char** argv) {
  const uint8_t key[16] = {0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6, 0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c};
  HopSchedule s(key);
  uint64_t first = strtoull(argv[1], nullptr, 10);
  int n = atoi(argv[2]);
  for (int i = 0; i < n; i++) printf("%d\n", (int)s.channel(first + i));
  return 0;
}
