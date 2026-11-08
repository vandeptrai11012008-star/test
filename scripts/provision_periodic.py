#!/usr/bin/env python3
"""Generate private firmware headers for one dog and up to four wristbands.

Read each wristband's factory ID with USB command #I. This tool writes keys only
to a NEW private directory; it never prints them or modifies the checkout.
"""
import argparse
import os
from pathlib import Path
import secrets


def device_id(text):
    try:
        value = int(text, 16)
    except ValueError:
        raise argparse.ArgumentTypeError("ID must be hexadecimal") from None
    if not 0 < value < 2**64:
        raise argparse.ArgumentTypeError("ID must be a nonzero 64-bit hexadecimal value")
    return value


def key_literal(key):
    return "{" + ", ".join(f"0x{byte:02x}" for byte in key) + "}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dog-id", type=device_id, required=True)
    ap.add_argument("--band-id", type=device_id, action="append", required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    if len(args.band_id) > 4 or len(set(args.band_id)) != len(args.band_id):
        ap.error("provide one to four distinct band IDs")
    checkout = Path(__file__).resolve().parents[1]
    destination = args.out_dir.resolve()
    if destination == checkout or checkout in destination.parents:
        ap.error("keep private pairing headers outside the repository")
    try:
        args.out_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        ap.error("output directory already exists; refusing to replace pairing keys")
    hop_key = key_literal(secrets.token_bytes(16))
    common = ("// PRIVATE pairing keys. Do not commit or share.\n#pragma once\n"
              f"#define GROUP_KEY_BYTES {hop_key}\n"
              f"#define PERIODIC_DOG_ID 0x{args.dog_id:016X}ULL\n")
    peers = []

    def write(name, content):
        path = args.out_dir / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(content)

    for band in args.band_id:
        pair_key = key_literal(secrets.token_bytes(16))
        peers.append(f"{{0x{band:016X}ULL, {pair_key}}}")
        write(f"band-{band:016X}-secrets.h", common + f"#define PERIODIC_PAIR_KEY_BYTES {pair_key}\n")
    write("dog-secrets.h", common + "#define PERIODIC_PEERS {" + ", ".join(peers) + "}\n")
    write("radio-secrets.h", "// PRIVATE shared hopping key.\n#pragma once\n"
          f"#define GROUP_KEY_BYTES {hop_key}\n")
    print(f"Created private headers in {args.out_dir}; no keys printed.")


if __name__ == "__main__":
    main()
