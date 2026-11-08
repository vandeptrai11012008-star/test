#include <stdio.h>
#include <string.h>
#include "periodic_dog.h"
static int fails = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); ++fails; } } while (0)
int main() {
  uint8_t key[16] = {}, key_b[16] = {1}, wrong[16] = {2};
  uint8_t n[4] = {1,2,3,4}, next[4] = {5,6,7,8};
  uint8_t chal[32], retry[32], reply[32], alive[32], bad[32], bits[3];
  const uint64_t dog_id = 0x10000000001ULL, band_a = 0xAA0000000001ULL, band_b = 0xBB0000000001ULL;
  const uint32_t slot = 500000;
  rescue::PeriodicDog a(key, dog_id, band_a), b(key_b, dog_id, band_b);
  rescue::PeriodicBand wrist_a(key, dog_id, band_a), wrist_b(key_b, dog_id, band_b);
  CHECK(!wrist_a.status_led(0)); CHECK(!wrist_b.status_led(0));
  CHECK(a.period() == 2);
  CHECK(a.challenge(100, slot, n, chal));
  CHECK(rescue::get_id(chal + 16) == dog_id && rescue::get_id(chal + 24) == band_a);
  CHECK(!a.due(2099)); CHECK(a.challenge(2100, slot, next, retry));
  CHECK(!memcmp(chal, retry, 32));
  CHECK(wrist_a.on_packet(chal,32,slot,2200,reply,bits) == rescue::PeriodicBand::PROMPT);
  CHECK(wrist_a.status_led(2200)); CHECK(!wrist_b.status_led(2200));
  CHECK(!wrist_a.on_packet(retry,32,slot,2201,bad,bits));
  CHECK(!wrist_b.on_packet(chal,32,slot,2200,bad,bits));
  CHECK(a.receive(reply,32,2300,slot));
  CHECK(a.state(2300) == rescue::PeriodicDog::BAND_SEEN);
  CHECK(!a.receive(reply,32,2301,slot)); CHECK(a.due(4100));
  CHECK(!wrist_b.press(2400,bad));
  rescue::make_beacon(wrong,rescue::BEACON_ALIVE,n,slot,dog_id,band_a,bad);
  CHECK(!a.receive(bad,32,3000,slot));
  rescue::make_beacon(key,rescue::BEACON_ALIVE,n,slot,dog_id,band_b,bad);
  CHECK(!a.receive(bad,32,3000,slot)); // even a correctly signed foreign ID fails
  rescue::make_beacon(key,rescue::BEACON_ALIVE,n,slot,dog_id+1,band_a,bad);
  CHECK(!a.receive(bad,32,3000,slot));
  rescue::make_beacon(key,rescue::BEACON_ALIVE,next,slot,dog_id,band_a,bad);
  CHECK(!a.receive(bad,32,3000,slot));
  CHECK(!a.receive(chal,32,3000,slot)); CHECK(!a.receive(reply,31,3000,slot));
  CHECK(wrist_a.press(5000,alive));
  CHECK(!wrist_a.press(5001,bad));
  // Lost ALIVE is recovered without another prompt on a retry of the same challenge.
  CHECK(wrist_a.on_packet(chal,32,slot,6000,retry,bits) == rescue::PeriodicBand::RETRY_CONFIRMATION);
  CHECK(!memcmp(alive,retry,32));
  CHECK(a.receive(retry,32,6000,slot));
  CHECK(a.remaining_seconds(6000) == 7200);
  CHECK(a.remaining_seconds(7000) == 7199);
  CHECK(a.remaining_seconds(7205999) == 1);
  CHECK(a.remaining_seconds(7206000) == 0);
  uint8_t first_ack[32];
  CHECK(a.receipt(retry,32,6001,slot,first_ack));
  CHECK(wrist_a.on_packet(first_ack,32,slot,6001,reply,bits) == rescue::PeriodicBand::ACKNOWLEDGED);
  CHECK(a.state(31000) == rescue::PeriodicDog::CONFIRMED);
  CHECK(!a.receive(alive,32,6001,slot)); CHECK(!a.due(31000));
  CHECK(!a.due(7205999)); CHECK(a.due(7206000));
  // One confirmed band does not stop discovery of the other.
  CHECK(b.challenge(31000,slot,n,chal));
  CHECK(wrist_b.on_packet(chal,32,slot,31001,reply,bits) == rescue::PeriodicBand::PROMPT);
  CHECK(!wrist_a.on_packet(chal,32,slot,31001,bad,bits));
  CHECK(b.receive(reply,32,31002,slot)); CHECK(b.due(33000));
  // A fresh request to A stays silent for exactly two hours after its button press.
  rescue::make_beacon(key,rescue::BEACON,next,slot+1,dog_id,band_a,chal);
  CHECK(!wrist_a.on_packet(chal,32,slot+1,7206000,reply,bits));
  CHECK(wrist_a.on_packet(chal,32,slot+1,7206001,reply,bits) == rescue::PeriodicBand::PROMPT);
  CHECK(a.challenge(7206000,slot+1,next,chal));
  CHECK(!a.receive(alive,32,7206001,slot+1));
  CHECK(a.set_period(0)); CHECK(!a.due(9999999));
  CHECK(!a.set_period(3601)); CHECK(a.set_period(60));
  CHECK(a.challenge(8000000,slot+2,n,chal));
  CHECK(!a.due(8030000)); CHECK(a.due(8060000));
  // Configuration/clock changes must not erase a two-hour cooldown.
  CHECK(wrist_b.press(32000,alive));
  CHECK(b.receive(alive,32,32001,slot));
  CHECK(b.set_period(5)); b.clear(); CHECK(!b.due(50000));
  // New instances: timeout, tampered IDs, legacy ALIVE, hour and millis rollover.
  rescue::PeriodicDog c(key,dog_id,band_a);
  rescue::PeriodicBand w(key,dog_id,band_a);
  CHECK(c.challenge(0xFFFFFF00UL,slot,n,chal));
  CHECK(w.on_packet(chal,32,slot,0xFFFFFF00UL,reply,bits) == rescue::PeriodicBand::PROMPT);
  CHECK(!c.due(0x100)); CHECK(c.due(0x700));
  CHECK(w.press(0x800,alive)); CHECK(c.receive(alive,32,0x800,slot+1));
  uint8_t ack[32];
  CHECK(!w.success_led(0x800)); // TX alone is not confirmed success
  CHECK(w.status_led(0x800)); // stay lit while awaiting the dog's receipt
  CHECK(c.receipt(alive,32,0x801,slot+1,ack));
  ack[31] ^= 1;
  CHECK(w.on_packet(ack,32,slot+1,0x802,reply,bits) == rescue::PeriodicBand::SILENT);
  ack[31] ^= 1;
  CHECK(w.retry_confirmation(0x800 + 2000,retry)); CHECK(!memcmp(alive,retry,32));
  CHECK(!c.receive(retry,32,0x800 + 2001,slot+1));
  CHECK(c.receipt(retry,32,0x800 + 2001,slot+1,ack));
  CHECK(w.on_packet(ack,32,slot+1,0x800 + 2002,reply,bits) == rescue::PeriodicBand::ACKNOWLEDGED);
  CHECK(w.success_led(0x800 + 2002)); CHECK(w.success_led(0x800 + 7001));
  CHECK(!w.success_led(0x800 + 7002));
  CHECK(!w.status_led(0x800 + 7002));
  CHECK(!w.retry_confirmation(0x800 + 4002,retry));
  CHECK(w.on_packet(ack,32,slot+1,0x800 + 4003,reply,bits) == rescue::PeriodicBand::SILENT);
  CHECK(!w.success_led(0x800 + 7002)); // duplicate ACK cannot prolong LED
  CHECK(!c.receipt(alive,32,0x800 + 30000,slot+1,ack));
  CHECK(!c.due(0x800 + rescue::CHECKIN_COOLDOWN_MS - 1));
  CHECK(c.due(0x800 + rescue::CHECKIN_COOLDOWN_MS));
  CHECK(c.remaining_seconds(0x800 + 1000) == 7199);
  rescue::PeriodicDog expired(key,dog_id,band_a);
  rescue::PeriodicBand expired_w(key,dog_id,band_a);
  CHECK(expired.challenge(1,slot,n,chal));
  CHECK(expired_w.on_packet(chal,32,slot,1,reply,bits) == rescue::PeriodicBand::PROMPT);
  CHECK(!expired_w.press(30001,bad));
  CHECK(!expired_w.status_led(30001));
  CHECK(!expired.receive(alive,32,30001,slot));
  CHECK(expired.challenge(30001,slot,next,chal));
  chal[31] ^= 1;
  CHECK(!expired_w.on_packet(chal,32,slot,30002,reply,bits));
  rescue::encode(rescue::make(key,rescue::ALIVE,next,slot),bad);
  CHECK(!expired.receive(bad,32,30002,slot));
  // Missing ACK must not impose a two-hour local cooldown or show success.
  rescue::PeriodicBand no_ack(key,dog_id,band_a);
  rescue::make_beacon(key,rescue::BEACON,n,slot,dog_id,band_a,chal);
  CHECK(no_ack.on_packet(chal,32,slot,1,reply,bits) == rescue::PeriodicBand::PROMPT);
  CHECK(no_ack.press(100,alive)); CHECK(!no_ack.cooling(101));
  CHECK(!no_ack.success_led(101)); CHECK(no_ack.retry_confirmation(2100,retry));
  CHECK(!no_ack.retry_confirmation(30100,retry));
  CHECK(!no_ack.status_led(30100));
  rescue::make_beacon(key,rescue::BEACON,next,slot,dog_id,band_a,chal);
  CHECK(no_ack.on_packet(chal,32,slot,30101,reply,bits) == rescue::PeriodicBand::PROMPT);
  // USB clear-wait cancels both cooldowns and pending receipts, preserving pairing.
  a.clear_wait(); wrist_a.clear_wait();
  CHECK(a.remaining_seconds(7206010) == 0);
  CHECK(!a.cooling(7206010)); CHECK(!wrist_a.cooling(7206010));
  CHECK(a.due(7206010)); CHECK(!wrist_a.status_led(7206010));
  CHECK(!a.receive(alive,32,7206010,slot));
  CHECK(!wrist_a.retry_confirmation(7206010,retry));
  CHECK(wrist_a.on_packet(first_ack,32,slot,7206010,reply,bits) == rescue::PeriodicBand::SILENT);
  uint8_t reset_nonce[4] = {9,8,7,6};
  CHECK(a.challenge(7206011,slot+2,reset_nonce,chal));
  CHECK(wrist_a.on_packet(chal,32,slot+2,7206012,reply,bits) == rescue::PeriodicBand::PROMPT);
  CHECK(a.receive(reply,32,7206013,slot+2));
  CHECK(!wrist_b.on_packet(chal,32,slot+2,7206013,bad,bits));
  rescue::PeriodicDog unpaired(key,0,band_a);
  CHECK(!unpaired.due(0));
  puts(fails ? "periodic dog: FAILED" : "periodic dog: ok");
  return fails ? 1 : 0;
}
