// The band firmware's logic as a host program: speaks the same USB serial framing on
// stdin/stdout as the ESP32 band in USB mode. Lets the console (and tests) exercise the
// C++ BandCore end to end without hardware. Not built into the firmware.
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "rescue_core.h"

static const uint8_t KEY[16] = {0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6,
                                0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c};
static long clock_offset = 0;  // set by "#T <unix>", like setting the band's RTC

static uint32_t now_slot() { return (uint32_t)((time(nullptr) + clock_offset) / rescue::SLOT_SECONDS); }

static void send_packet(const uint8_t p[32]) {
  fputc(0x20, stdout);
  fwrite(p, 1, 32, stdout);
  fflush(stdout);
}

int main() {
  rescue::BandCore band(KEY);
  rescue::FrameParser parser;
  printf("#HELLO band host\n");
  fflush(stdout);
  int c;
  while ((c = getchar()) != EOF) {
    auto ev = parser.feed((uint8_t)c);
    if (ev == rescue::FrameParser::PACKET) {
      uint8_t reply[32], bits[3];
      if (band.on_packet(parser.packet(), 32, now_slot(), reply, bits)) {
        send_packet(reply);
        printf("#BUZZ %d %d %d\n", bits[0], bits[1], bits[2]);
      } else {
        printf("#SILENT\n");
      }
      fflush(stdout);
    } else if (ev == rescue::FrameParser::LINE) {
      const char* l = parser.line();
      if (l[1] == 'T') {
        clock_offset = atol(l + 2) - (long)time(nullptr);
        printf("#TIME %u\n", now_slot());
      } else if (l[1] == 'P') {
        uint8_t alive[32];
        if (band.press(alive)) { send_packet(alive); printf("#PRESS\n"); }
        else printf("#PRESS none\n");
      } else if (l[1] == '?') {
        printf("#OK band host\n");
      }
      fflush(stdout);
    }
  }
  return 0;
}
