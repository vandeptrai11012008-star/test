// Host test for lib/rescue_core: same vectors as tests/test_protocol.py, plus band scenarios.
// Build and run:  c++ -std=c++17 -I lib/rescue_core lib/rescue_core/rescue_core.cpp test/host/test_core.cpp -o /tmp/t && /tmp/t
#include <stdio.h>
#include <string.h>

#include "rescue_core.h"

static int failures = 0;
#define CHECK(cond)                                                \
  do {                                                             \
    if (!(cond)) { printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); failures++; } \
  } while (0)

static void hex(const uint8_t* b, size_t n, char* out) {
  for (size_t i = 0; i < n; i++) snprintf(out + 2 * i, 3, "%02x", b[i]);
}
static bool hex_is(const uint8_t* b, size_t n, const char* want) {
  char got[129];
  hex(b, n, got);
  if (strcmp(got, want) != 0) { printf("  got %s want %s\n", got, want); return false; }
  return true;
}

static const uint8_t KEY[16] = {0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6,
                                0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c};
static const uint32_t SLOT = 498350;  // 2026-11-07 14:07 UTC, as in the Python tests
static const uint8_t NONCE[4] = {1, 2, 3, 4};

int main() {
  uint8_t out[32];

  // SHA-256 and HMAC-SHA256 against published vectors (FIPS 180-2, RFC 4231 case 2).
  rescue::sha256((const uint8_t*)"abc", 3, out);
  CHECK(hex_is(out, 32, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
  const char* msg = "what do ya want for nothing?";
  rescue::hmac_sha256((const uint8_t*)"Jefe", 4, (const uint8_t*)msg, strlen(msg), out);
  CHECK(hex_is(out, 32, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"));

  // Protocol vectors shared with rescue/protocol.py and the web sim.
  rescue::tag(KEY, rescue::CHALLENGE, NONCE, SLOT, out);
  CHECK(hex_is(out, 8, "888adfa9b70cbdca"));
  rescue::tag(KEY, rescue::REPLY, NONCE, SLOT, out);
  CHECK(hex_is(out, 8, "f07c4644e68a9d2c"));
  rescue::tag(KEY, rescue::ALIVE, NONCE, SLOT, out);
  CHECK(hex_is(out, 8, "36261e5e924714bb"));
  uint8_t bits[3];
  rescue::blink_bits(KEY, SLOT, bits);
  CHECK(bits[0] == 1 && bits[1] == 0 && bits[2] == 0);

  // Packet layout round trip.
  uint8_t raw[32];
  rescue::encode(rescue::make(KEY, rescue::CHALLENGE, NONCE, SLOT), raw);
  CHECK(raw[0] == 1 && raw[1] == 1 && raw[6] == ((SLOT >> 8) & 0xff) && raw[7] == (SLOT & 0xff));
  rescue::Packet p;
  CHECK(rescue::decode(raw, 32, p) && p.slot16 == (SLOT & 0xffff));
  CHECK(!rescue::decode(raw, 31, p));

  // Band: happy path, replayed challenge ignored, ALIVE reuses the nonce.
  rescue::BandCore band(KEY);
  uint8_t reply[32], alive[32];
  CHECK(!band.press(alive));
  CHECK(band.on_packet(raw, 32, SLOT, reply, bits));
  CHECK(reply[0] == rescue::REPLY && memcmp(reply + 2, NONCE, 4) == 0 && hex_is(reply + 8, 8, "f07c4644e68a9d2c"));
  CHECK(!band.on_packet(raw, 32, SLOT, reply, bits));
  CHECK(band.press(alive) && alive[0] == rescue::ALIVE && hex_is(alive + 8, 8, "36261e5e924714bb"));

  // Band: spoofed key, wrong type and drift outside the window all get silence.
  rescue::BandCore fresh(KEY);
  const uint8_t bad_key[16] = {0};
  uint8_t spoof[32];
  rescue::encode(rescue::make(bad_key, rescue::CHALLENGE, NONCE, SLOT), spoof);
  CHECK(!fresh.on_packet(spoof, 32, SLOT, reply, bits));
  rescue::encode(rescue::make(KEY, rescue::REPLY, NONCE, SLOT), spoof);
  CHECK(!fresh.on_packet(spoof, 32, SLOT, reply, bits));
  const int offs[3] = {1, -1, 2};
  const bool ok[3] = {true, true, false};
  for (int i = 0; i < 3; i++) {
    rescue::BandCore b(KEY);
    CHECK(b.on_packet(raw, 32, SLOT + offs[i], reply, bits) == ok[i]);
  }

  // Dog packets: byte-for-byte the same as rescue/protocol.py (vectors below come from Python).
  uint8_t dog[32], dn[4];
  uint32_t ds = 0;
  rescue::make_dog_check(KEY, NONCE, 498358, dog);
  CHECK(hex_is(dog, 32, "0401010203049ab6d51e0709821d0c6600079ab6000000000000000000000000"));
  CHECK(rescue::parse_dog_check(KEY, dog, 32, dn, &ds) && ds == 498358 && memcmp(dn, NONCE, 4) == 0);
  dog[19] ^= 1;  // tampered slot
  CHECK(!rescue::parse_dog_check(KEY, dog, 32, dn, &ds));
  rescue::make_dog_verdict(KEY, NONCE, 498358, true, true, -48, 9, 151, dog);
  CHECK(hex_is(dog, 32, "0501010203049ab6a286f1c692c20d0b0101d009009700000000000000000000"));
  rescue::Packet dp;
  CHECK(!rescue::decode(dog, 32, dp));  // band and drone ignore dog packets
  rescue::BandCore dband(KEY);
  CHECK(!dband.on_packet(dog, 32, 498358, reply, bits));

  // Framing: a packet frame and a text line.
  rescue::FrameParser fp;
  int packets = 0, lines = 0;
  uint8_t stream[1 + 32 + 12];
  stream[0] = 0x20;
  memcpy(stream + 1, raw, 32);
  memcpy(stream + 33, "#T 12345678\n", 12);
  for (uint8_t b : stream) {
    auto ev = fp.feed(b);
    if (ev == rescue::FrameParser::PACKET) { packets++; CHECK(memcmp(fp.packet(), raw, 32) == 0); }
    if (ev == rescue::FrameParser::LINE) { lines++; CHECK(strcmp(fp.line(), "#T 12345678") == 0); }
  }
  CHECK(packets == 1 && lines == 1);

  printf(failures ? "firmware core: %d FAILED\n" : "firmware core: all checks passed\n", failures);
  return failures ? 1 : 0;
}
