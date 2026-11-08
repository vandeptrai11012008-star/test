// Rescue band protocol core. Portable C++ (no Arduino), so the same code runs on the
// ESP32 and in the host tests that check it against rescue/protocol.py.
//
// Packet (32 bytes): TYPE(1) VER(1) NONCE(4) SLOT16(2, big endian) HMAC8(8) PAD(16)
// Tag:   HMAC-SHA256(key, TYPE || NONCE || SLOT_u32_BE)[:8]
// Slots: unix_seconds / 3600. Receivers accept slot-1, slot, slot+1 (tried 0, -1, +1).
// Blink: HMAC(key, "BLINK" || SLOT_u32_BE)[0], bits 0..2 (0 = SHORT, 1 = LONG).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace rescue {

constexpr uint8_t CHALLENGE = 1, REPLY = 2, ALIVE = 3, VERSION = 1;
constexpr size_t PACKET_LEN = 32, KEY_LEN = 16, TAG_LEN = 8, NONCE_LEN = 4;
constexpr uint32_t SLOT_SECONDS = 3600;

struct Packet {
  uint8_t type;
  uint8_t nonce[NONCE_LEN];
  uint16_t slot16;
  uint8_t mac[TAG_LEN];
};

void sha256(const uint8_t* data, size_t len, uint8_t out[32]);
void hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* msg, size_t msg_len, uint8_t out[32]);
void tag(const uint8_t key[KEY_LEN], uint8_t type, const uint8_t nonce[NONCE_LEN], uint32_t slot,
         uint8_t out[TAG_LEN]);
void blink_bits(const uint8_t key[KEY_LEN], uint32_t slot, uint8_t bits[3]);
bool ct_equal(const uint8_t* a, const uint8_t* b, size_t n);  // constant time

void encode(const Packet& p, uint8_t out[PACKET_LEN]);
bool decode(const uint8_t* raw, size_t len, Packet& out);  // false: bad length, version or type
Packet make(const uint8_t key[KEY_LEN], uint8_t type, const uint8_t nonce[NONCE_LEN], uint32_t slot);
bool verify(const uint8_t key[KEY_LEN], const Packet& p, uint32_t now_slot, uint32_t* matched);

// ---- Dog packets (see rescue/protocol.py). The tag also covers DATA in the first PAD bytes:
//   tag = HMAC(key, TYPE || NONCE || SLOT_u32_BE || DATA)[:8]
constexpr uint8_t DOG_CHECK = 4, DOG_VERDICT = 5;
constexpr uint16_t NO_REPLY_MS = 0xFFFF;
void tag_ext(const uint8_t key[KEY_LEN], uint8_t type, const uint8_t nonce[NONCE_LEN], uint32_t slot,
             const uint8_t* data, size_t data_len, uint8_t out[TAG_LEN]);
void make_dog_check(const uint8_t key[KEY_LEN], const uint8_t nonce[NONCE_LEN], uint32_t slot,
                    uint8_t out[PACKET_LEN]);
// Valid order from the drone: fills nonce and the full slot (the dog has no clock).
bool parse_dog_check(const uint8_t key[KEY_LEN], const uint8_t* raw, size_t len, uint8_t nonce[NONCE_LEN],
                     uint32_t* slot);
void make_dog_verdict(const uint8_t key[KEY_LEN], const uint8_t nonce[NONCE_LEN], uint32_t slot, bool ok,
                      bool tag_ok, int8_t rssi, int8_t snr, uint16_t rtt_ms, uint8_t out[PACKET_LEN]);

// Port of rescue/band.py. Silent (returns false) on any failure.
class BandCore {
 public:
  explicit BandCore(const uint8_t key[KEY_LEN]);
  // Verified challenge: fills reply (32 bytes) and the blink bits, returns true.
  bool on_packet(const uint8_t* raw, size_t len, uint32_t now_slot, uint8_t reply[PACKET_LEN], uint8_t bits[3]);
  // Button: signed ALIVE for the last verified challenge. False if there is none.
  bool press(uint8_t out[PACKET_LEN]) const;
  void forget();  // what a reboot does: RAM nonce cache and last challenge are gone

 private:
  static constexpr int CACHE = 64;  // RAM only; oldest nonce is evicted first
  uint8_t key_[KEY_LEN];
  uint8_t seen_[CACHE][NONCE_LEN];
  int seen_count_ = 0, seen_head_ = 0;
  bool has_last_ = false;
  uint8_t last_nonce_[NONCE_LEN];
  uint32_t last_slot_ = 0;
  bool seen(const uint8_t nonce[NONCE_LEN]) const;
  void remember(const uint8_t nonce[NONCE_LEN]);
};

// USB serial framing shared by the band (USB test mode) and the dongle:
//   0x20 + 32 bytes   one packet
//   '#' ... '\n'      a text line (host commands such as "#T <unix>", device logs)
// Any other byte between frames is ignored.
class FrameParser {
 public:
  enum Event { NONE, PACKET, LINE };
  Event feed(uint8_t b);
  const uint8_t* packet() const { return buf_; }
  const char* line() const { return line_; }

 private:
  enum State { IDLE, IN_PACKET, IN_LINE } state_ = IDLE;
  uint8_t buf_[PACKET_LEN];
  char line_[96];
  size_t n_ = 0;
};

}  // namespace rescue
