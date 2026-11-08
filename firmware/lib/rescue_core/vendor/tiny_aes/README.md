# Vendored AES primitive

Source: https://github.com/kokke/tiny-AES-c
Commit: `23856752fbd139da0b8ca6e471a13d5bcc99a08d`
License: Unlicense, included in `unlicense.txt`.

Local integration changes: CBC/CTR APIs disabled by default (CCM constructs its
own counters), C linkage guards for C++ callers, and AVR PROGMEM access for AES
lookup tables to avoid consuming scarce SRAM. The AES round/key schedule logic
is otherwise unchanged. The ECB encrypt function is used only as the AES block
primitive inside CCM, never as the radio encryption mode.
