#pragma once
#include <stddef.h>
#include <stdint.h>

namespace rescue {
// CCM with AES-128, a 13-byte nonce (L=2), and an 8-byte authentication tag.
// AAD < 0xff00 bytes, plaintext <= 65535 bytes. Nonces MUST be unique per key.
// Exact retries may reuse the exact same sealed packet, never a changed message.
bool ccm_seal(const uint8_t key[16], const uint8_t nonce[13], const uint8_t* aad, size_t aad_len,
              const uint8_t* plain, size_t len, uint8_t* cipher, uint8_t tag[8]);
// Auth failure zeroes the candidate plaintext; unauthenticated content is not used.
bool ccm_open(const uint8_t key[16], const uint8_t nonce[13], const uint8_t* aad, size_t aad_len,
              const uint8_t* cipher, size_t len, const uint8_t tag[8], uint8_t* plain);
}
