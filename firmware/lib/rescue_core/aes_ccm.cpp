#include "aes_ccm.h"
#include "vendor/tiny_aes/aes.h"
#include <string.h>

namespace rescue {
namespace {
void wipe(void* ptr, size_t n) { volatile uint8_t* p = (volatile uint8_t*)ptr; while (n--) *p++ = 0; }
void mac_block(const AES_ctx& ctx, uint8_t state[16], const uint8_t block[16]) {
  for (uint8_t i = 0; i < 16; ++i) state[i] ^= block[i];
  AES_ECB_encrypt(&ctx, state);
}
void authenticate(const AES_ctx& ctx, const uint8_t nonce[13], const uint8_t* aad, size_t aad_len,
                  const uint8_t* plain, size_t len, uint8_t tag[8]) {
  uint8_t state[16] = {}, block[16] = {};
  block[0] = (aad_len ? 0x40 : 0) | 0x18 | 1; // M=8, L=2
  memcpy(block + 1, nonce, 13); block[14] = len >> 8; block[15] = len;
  mac_block(ctx, state, block);
  if (aad_len) {
    memset(block, 0, 16); block[0] = aad_len >> 8; block[1] = aad_len;
    size_t used = aad_len < 14 ? aad_len : 14;
    memcpy(block + 2, aad, used); mac_block(ctx, state, block);
    while (used < aad_len) {
      memset(block, 0, 16);
      size_t n = aad_len - used; if (n > 16) n = 16;
      memcpy(block, aad + used, n); mac_block(ctx, state, block); used += n;
    }
  }
  for (size_t pos = 0; pos < len;) {
    memset(block, 0, 16);
    size_t n = len - pos; if (n > 16) n = 16;
    memcpy(block, plain + pos, n); mac_block(ctx, state, block); pos += n;
  }
  block[0] = 1; memcpy(block + 1, nonce, 13); block[14] = block[15] = 0;
  AES_ECB_encrypt(&ctx, block);
  for (uint8_t i = 0; i < 8; ++i) tag[i] = state[i] ^ block[i];
  wipe(state, sizeof state); wipe(block, sizeof block);
}
void crypt(const AES_ctx& ctx, const uint8_t nonce[13], const uint8_t* in, size_t len, uint8_t* out) {
  uint8_t block[16]; uint16_t counter = 1;
  for (size_t pos = 0; pos < len; ++counter) {
    block[0] = 1; memcpy(block + 1, nonce, 13); block[14] = counter >> 8; block[15] = counter;
    AES_ECB_encrypt(&ctx, block);
    size_t n = len - pos; if (n > 16) n = 16;
    for (size_t i = 0; i < n; ++i) out[pos + i] = in[pos + i] ^ block[i];
    pos += n;
  }
  wipe(block, sizeof block);
}
}
bool ccm_seal(const uint8_t key[16], const uint8_t nonce[13], const uint8_t* aad, size_t aad_len,
              const uint8_t* plain, size_t len, uint8_t* cipher, uint8_t tag[8]) {
  if (len > 65535UL || aad_len >= 0xff00UL) return false;
  AES_ctx ctx; AES_init_ctx(&ctx, key);
  authenticate(ctx, nonce, aad, aad_len, plain, len, tag);
  crypt(ctx, nonce, plain, len, cipher);
  wipe(&ctx, sizeof ctx); return true;
}
bool ccm_open(const uint8_t key[16], const uint8_t nonce[13], const uint8_t* aad, size_t aad_len,
              const uint8_t* cipher, size_t len, const uint8_t tag[8], uint8_t* plain) {
  if (len > 65535UL || aad_len >= 0xff00UL) return false;
  AES_ctx ctx; AES_init_ctx(&ctx, key);
  crypt(ctx, nonce, cipher, len, plain);
  uint8_t expected[8], diff = 0;
  authenticate(ctx, nonce, aad, aad_len, plain, len, expected);
  for (uint8_t i = 0; i < 8; ++i) diff |= expected[i] ^ tag[i];
  wipe(&ctx, sizeof ctx); wipe(expected, sizeof expected);
  if (diff) wipe(plain, len);
  return diff == 0;
}
}
