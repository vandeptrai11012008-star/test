#include "rescue_core.h"

#include <string.h>

namespace rescue {
namespace {

const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

inline uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

struct Sha {
  uint32_t h[8];
  uint8_t buf[64];
  size_t blen;
  uint64_t total;
};

void sha_init(Sha& s) {
  const uint32_t h0[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                          0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
  memcpy(s.h, h0, sizeof h0);
  s.blen = 0;
  s.total = 0;
}

void sha_block(Sha& s, const uint8_t* p) {
  uint32_t w[64];
  for (int i = 0; i < 16; i++)
    w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
  for (int i = 16; i < 64; i++) {
    uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
    uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
    w[i] = w[i - 16] + s0 + w[i - 7] + s1;
  }
  uint32_t a = s.h[0], b = s.h[1], c = s.h[2], d = s.h[3], e = s.h[4], f = s.h[5], g = s.h[6], h = s.h[7];
  for (int i = 0; i < 64; i++) {
    uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
    uint32_t ch = (e & f) ^ (~e & g);
    uint32_t t1 = h + S1 + ch + K[i] + w[i];
    uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
    uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
    uint32_t t2 = S0 + maj;
    h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
  }
  s.h[0] += a; s.h[1] += b; s.h[2] += c; s.h[3] += d;
  s.h[4] += e; s.h[5] += f; s.h[6] += g; s.h[7] += h;
}

void sha_update(Sha& s, const uint8_t* d, size_t n) {
  s.total += n;
  while (n) {
    size_t take = 64 - s.blen < n ? 64 - s.blen : n;
    memcpy(s.buf + s.blen, d, take);
    s.blen += take;
    d += take;
    n -= take;
    if (s.blen == 64) {
      sha_block(s, s.buf);
      s.blen = 0;
    }
  }
}

void sha_final(Sha& s, uint8_t out[32]) {
  uint64_t bits = s.total * 8;
  s.buf[s.blen++] = 0x80;
  if (s.blen > 56) {
    memset(s.buf + s.blen, 0, 64 - s.blen);
    sha_block(s, s.buf);
    s.blen = 0;
  }
  memset(s.buf + s.blen, 0, 56 - s.blen);
  for (int i = 0; i < 8; i++) s.buf[56 + i] = (uint8_t)(bits >> (56 - 8 * i));
  sha_block(s, s.buf);
  for (int i = 0; i < 8; i++) {
    out[4 * i] = s.h[i] >> 24;
    out[4 * i + 1] = s.h[i] >> 16;
    out[4 * i + 2] = s.h[i] >> 8;
    out[4 * i + 3] = s.h[i];
  }
}

void be32(uint32_t v, uint8_t out[4]) {
  out[0] = v >> 24; out[1] = v >> 16; out[2] = v >> 8; out[3] = v;
}

}  // namespace

void sha256(const uint8_t* data, size_t len, uint8_t out[32]) {
  Sha s;
  sha_init(s);
  sha_update(s, data, len);
  sha_final(s, out);
}

void hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* msg, size_t msg_len, uint8_t out[32]) {
  uint8_t k0[64] = {0};
  if (key_len > 64) sha256(key, key_len, k0); else memcpy(k0, key, key_len);
  uint8_t pad[64], inner[32];
  Sha s;
  for (int i = 0; i < 64; i++) pad[i] = k0[i] ^ 0x36;
  sha_init(s); sha_update(s, pad, 64); sha_update(s, msg, msg_len); sha_final(s, inner);
  for (int i = 0; i < 64; i++) pad[i] = k0[i] ^ 0x5c;
  sha_init(s); sha_update(s, pad, 64); sha_update(s, inner, 32); sha_final(s, out);
}

void tag(const uint8_t key[KEY_LEN], uint8_t type, const uint8_t nonce[NONCE_LEN], uint32_t slot,
         uint8_t out[TAG_LEN]) {
  uint8_t msg[1 + NONCE_LEN + 4], full[32];
  msg[0] = type;
  memcpy(msg + 1, nonce, NONCE_LEN);
  be32(slot, msg + 1 + NONCE_LEN);
  hmac_sha256(key, KEY_LEN, msg, sizeof msg, full);
  memcpy(out, full, TAG_LEN);
}

void tag_ext(const uint8_t key[KEY_LEN], uint8_t type, const uint8_t nonce[NONCE_LEN], uint32_t slot,
             const uint8_t* data, size_t data_len, uint8_t out[TAG_LEN]) {
  uint8_t msg[1 + NONCE_LEN + 4 + 16], full[32];
  if (data_len > 16) data_len = 16;
  msg[0] = type;
  memcpy(msg + 1, nonce, NONCE_LEN);
  be32(slot, msg + 1 + NONCE_LEN);
  memcpy(msg + 1 + NONCE_LEN + 4, data, data_len);
  hmac_sha256(key, KEY_LEN, msg, 1 + NONCE_LEN + 4 + data_len, full);
  memcpy(out, full, TAG_LEN);
}

namespace {
void frame(uint8_t type, const uint8_t nonce[NONCE_LEN], uint32_t slot, const uint8_t mac[TAG_LEN],
           const uint8_t* data, size_t data_len, uint8_t out[PACKET_LEN]) {
  memset(out, 0, PACKET_LEN);
  out[0] = type;
  out[1] = VERSION;
  memcpy(out + 2, nonce, NONCE_LEN);
  out[6] = (slot >> 8) & 0xff;
  out[7] = slot & 0xff;
  memcpy(out + 8, mac, TAG_LEN);
  memcpy(out + 16, data, data_len);
}
}  // namespace

void make_dog_check(const uint8_t key[KEY_LEN], const uint8_t nonce[NONCE_LEN], uint32_t slot,
                    uint8_t out[PACKET_LEN]) {
  uint8_t data[4], mac[TAG_LEN];
  be32(slot, data);
  tag_ext(key, DOG_CHECK, nonce, slot, data, 4, mac);
  frame(DOG_CHECK, nonce, slot, mac, data, 4, out);
}

bool parse_dog_check(const uint8_t key[KEY_LEN], const uint8_t* raw, size_t len, uint8_t nonce[NONCE_LEN],
                     uint32_t* slot) {
  if (len != PACKET_LEN || raw[0] != DOG_CHECK || raw[1] != VERSION) return false;
  uint32_t s = (uint32_t)raw[16] << 24 | (uint32_t)raw[17] << 16 | (uint32_t)raw[18] << 8 | raw[19];
  if ((s & 0xffff) != (uint16_t)(raw[6] << 8 | raw[7])) return false;
  uint8_t expect[TAG_LEN];
  tag_ext(key, DOG_CHECK, raw + 2, s, raw + 16, 4, expect);
  if (!ct_equal(expect, raw + 8, TAG_LEN)) return false;
  memcpy(nonce, raw + 2, NONCE_LEN);
  *slot = s;
  return true;
}

void make_dog_verdict(const uint8_t key[KEY_LEN], const uint8_t nonce[NONCE_LEN], uint32_t slot, bool ok,
                      bool tag_ok, int8_t rssi, int8_t snr, uint16_t rtt_ms, uint8_t out[PACKET_LEN]) {
  uint8_t data[6] = {(uint8_t)ok, (uint8_t)tag_ok, (uint8_t)rssi, (uint8_t)snr, (uint8_t)(rtt_ms >> 8),
                     (uint8_t)(rtt_ms & 0xff)};
  uint8_t mac[TAG_LEN];
  tag_ext(key, DOG_VERDICT, nonce, slot, data, 6, mac);
  frame(DOG_VERDICT, nonce, slot, mac, data, 6, out);
}

void blink_bits(const uint8_t key[KEY_LEN], uint32_t slot, uint8_t bits[3]) {
  uint8_t msg[9] = {'B', 'L', 'I', 'N', 'K'}, full[32];
  be32(slot, msg + 5);
  hmac_sha256(key, KEY_LEN, msg, sizeof msg, full);
  for (int i = 0; i < 3; i++) bits[i] = (full[0] >> i) & 1;
}

bool ct_equal(const uint8_t* a, const uint8_t* b, size_t n) {
  uint8_t diff = 0;
  for (size_t i = 0; i < n; i++) diff |= a[i] ^ b[i];
  return diff == 0;
}

void encode(const Packet& p, uint8_t out[PACKET_LEN]) {
  memset(out, 0, PACKET_LEN);
  out[0] = p.type;
  out[1] = VERSION;
  memcpy(out + 2, p.nonce, NONCE_LEN);
  out[6] = p.slot16 >> 8;
  out[7] = p.slot16 & 0xff;
  memcpy(out + 8, p.mac, TAG_LEN);
}

bool decode(const uint8_t* raw, size_t len, Packet& out) {
  if (len != PACKET_LEN || raw[1] != VERSION) return false;
  if (raw[0] != CHALLENGE && raw[0] != REPLY && raw[0] != ALIVE) return false;
  out.type = raw[0];
  memcpy(out.nonce, raw + 2, NONCE_LEN);
  out.slot16 = (uint16_t)(raw[6] << 8 | raw[7]);
  memcpy(out.mac, raw + 8, TAG_LEN);
  return true;
}

Packet make(const uint8_t key[KEY_LEN], uint8_t type, const uint8_t nonce[NONCE_LEN], uint32_t slot) {
  Packet p;
  p.type = type;
  memcpy(p.nonce, nonce, NONCE_LEN);
  p.slot16 = slot & 0xffff;
  tag(key, type, nonce, slot, p.mac);
  return p;
}

bool verify(const uint8_t key[KEY_LEN], const Packet& p, uint32_t now_slot, uint32_t* matched) {
  const int offs[3] = {0, -1, 1};  // same order as protocol.DRIFT_SLOTS
  for (int off : offs) {
    uint32_t s = now_slot + off;
    if ((s & 0xffff) != p.slot16) continue;
    uint8_t expect[TAG_LEN];
    tag(key, p.type, p.nonce, s, expect);
    if (ct_equal(expect, p.mac, TAG_LEN)) {
      if (matched) *matched = s;
      return true;
    }
  }
  return false;
}

BandCore::BandCore(const uint8_t key[KEY_LEN]) { memcpy(key_, key, KEY_LEN); }

bool BandCore::seen(const uint8_t nonce[NONCE_LEN]) const {
  for (int i = 0; i < seen_count_; i++)
    if (memcmp(seen_[i], nonce, NONCE_LEN) == 0) return true;
  return false;
}

void BandCore::remember(const uint8_t nonce[NONCE_LEN]) {
  memcpy(seen_[seen_head_], nonce, NONCE_LEN);
  seen_head_ = (seen_head_ + 1) % CACHE;
  if (seen_count_ < CACHE) seen_count_++;
}

bool BandCore::on_packet(const uint8_t* raw, size_t len, uint32_t now_slot, uint8_t reply[PACKET_LEN],
                         uint8_t bits[3]) {
  Packet p;
  if (!decode(raw, len, p)) return false;
  if (p.type != CHALLENGE || seen(p.nonce)) return false;
  uint32_t slot;
  if (!verify(key_, p, now_slot, &slot)) return false;
  remember(p.nonce);
  has_last_ = true;
  memcpy(last_nonce_, p.nonce, NONCE_LEN);
  last_slot_ = slot;
  blink_bits(key_, slot, bits);
  encode(make(key_, REPLY, p.nonce, slot), reply);
  return true;
}

bool BandCore::press(uint8_t out[PACKET_LEN]) const {
  if (!has_last_) return false;
  encode(make(key_, ALIVE, last_nonce_, last_slot_), out);
  return true;
}

void BandCore::forget() {
  seen_count_ = seen_head_ = 0;
  has_last_ = false;
}

FrameParser::Event FrameParser::feed(uint8_t b) {
  switch (state_) {
    case IDLE:
      if (b == 0x20) { state_ = IN_PACKET; n_ = 0; }
      else if (b == '#') { state_ = IN_LINE; n_ = 0; line_[n_++] = '#'; }
      return NONE;
    case IN_PACKET:
      buf_[n_++] = b;
      if (n_ == PACKET_LEN) { state_ = IDLE; return PACKET; }
      return NONE;
    case IN_LINE:
      if (b == '\n' || b == '\r') { line_[n_] = 0; state_ = IDLE; return LINE; }
      if (n_ < sizeof line_ - 1) line_[n_++] = (char)b;
      return NONE;
  }
  return NONE;
}

}  // namespace rescue
