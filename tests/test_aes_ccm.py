"""Native production CCM against RFC 3610 and an independent AESCCM implementation."""
import ctypes
from pathlib import Path
import shutil
import subprocess

from cryptography.hazmat.primitives.ciphers.aead import AESCCM
import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "firmware/lib/rescue_core"


@pytest.fixture(scope="module")
def native_ccm(tmp_path_factory):
    compiler = shutil.which("c++")
    if not compiler:
        pytest.skip("native compiler not installed")
    temp = tmp_path_factory.mktemp("ccm")
    wrapper = temp / "wrapper.cpp"
    wrapper.write_text('''#include "aes_ccm.h"
extern "C" bool seal(const unsigned char* k,const unsigned char* n,const unsigned char* a,unsigned al,const unsigned char* p,unsigned l,unsigned char* c,unsigned char* t) {return rescue::ccm_seal(k,n,a,al,p,l,c,t);}
extern "C" bool open_packet(const unsigned char* k,const unsigned char* n,const unsigned char* a,unsigned al,const unsigned char* c,unsigned l,const unsigned char* t,unsigned char* p) {return rescue::ccm_open(k,n,a,al,c,l,t,p);}
''')
    library = temp / "ccm.so"
    subprocess.run([compiler, "-std=c++17", "-O1", "-shared", "-fPIC", "-I", str(CORE),
                    str(CORE / "aes_ccm.cpp"), str(CORE / "vendor/tiny_aes/aes.c"),
                    str(wrapper), "-o", str(library)], check=True)
    lib = ctypes.CDLL(str(library))
    lib.seal.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint,
                         ctypes.c_char_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    lib.seal.restype = ctypes.c_bool
    lib.open_packet.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint,
                                ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_void_p]
    lib.open_packet.restype = ctypes.c_bool
    return lib


def seal(lib, key, nonce, aad, plain):
    cipher, tag = ctypes.create_string_buffer(len(plain)), ctypes.create_string_buffer(8)
    assert lib.seal(key, nonce, aad, len(aad), plain, len(plain), cipher, tag)
    return cipher.raw + tag.raw


def decrypt(lib, key, nonce, aad, packet):
    plain = ctypes.create_string_buffer(b"x" * (len(packet) - 8), len(packet) - 8)
    ok = lib.open_packet(key, nonce, aad, len(aad), packet[:-8], len(packet) - 8, packet[-8:], plain)
    return ok, plain.raw


def test_rfc3610_packet_vector_one(native_ccm):
    key = bytes(range(0xC0, 0xD0))
    nonce = bytes.fromhex("00000003020100a0a1a2a3a4a5")
    aad, plain = bytes(range(8)), bytes(range(8, 31))
    expected = bytes.fromhex("588c979a61c663d2f066d0c2c0f989806d5f6b61dac38417e8d12cfdf926e0")
    assert AESCCM(key, tag_length=8).encrypt(nonce, plain, aad) == expected
    assert seal(native_ccm, key, nonce, aad, plain) == expected
    assert decrypt(native_ccm, key, nonce, aad, expected) == (True, plain)


@pytest.mark.parametrize("length", [0, 1, 15, 16, 17, 23, 32, 64])
@pytest.mark.parametrize("aad_length", [0, 8, 15, 30])
def test_matches_independent_ccm_for_block_boundaries(native_ccm, length, aad_length):
    key, nonce = bytes(range(16)), bytes(range(13))
    plain = bytes((i * 13 + length) % 256 for i in range(length))
    aad = bytes((i * 7) % 256 for i in range(aad_length))
    expected = AESCCM(key, tag_length=8).encrypt(nonce, plain, aad)
    assert seal(native_ccm, key, nonce, aad, plain) == expected
    assert decrypt(native_ccm, key, nonce, aad, expected) == (True, plain)


def test_rejects_tampering_and_erases_candidate_plaintext(native_ccm):
    key, nonce, aad, plain = bytes(range(16)), bytes(range(13)), bytes(range(8)), bytes(range(16))
    packet = seal(native_ccm, key, nonce, aad, plain)
    for at in range(len(packet)):
        corrupt = bytearray(packet)
        corrupt[at] ^= 1
        assert decrypt(native_ccm, key, nonce, aad, bytes(corrupt)) == (False, bytes(16))
    assert decrypt(native_ccm, bytes(16), nonce, aad, packet) == (False, bytes(16))
    assert decrypt(native_ccm, key, bytes(13), aad, packet) == (False, bytes(16))
    assert decrypt(native_ccm, key, nonce, bytes(8), packet) == (False, bytes(16))
