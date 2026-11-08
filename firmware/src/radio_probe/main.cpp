// USB/SPI diagnosis only: no packet engine, transmit mode, pairing keys or RadioLib.
#include <Arduino.h>
#include <SPI.h>

constexpr uint8_t CS = 8, RESET = 4;

uint8_t reg(uint8_t address) {
  SPI.beginTransaction(SPISettings(250000, MSBFIRST, SPI_MODE0));
  digitalWrite(CS, LOW);
  SPI.transfer(address & 0x7f);
  uint8_t value = SPI.transfer(0);
  digitalWrite(CS, HIGH);
  SPI.endTransaction();
  return value;
}

void hex_byte(uint8_t value) {
  if (value < 16) Serial.print('0');
  Serial.print(value, HEX);
}

void sample() {
  Serial.print(F("#SPI cs=8 reset=4 RF69_VERSION="));
  for (uint8_t i = 0; i < 3; ++i) {
    hex_byte(reg(0x10)); Serial.print(' ');
  }
  Serial.print(F("SX127X_VERSION=")); hex_byte(reg(0x42));
  Serial.print(F(" OPMODE=")); hex_byte(reg(0x01));
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  pinMode(CS, OUTPUT);
  digitalWrite(CS, HIGH);
  SPI.begin();
  // RFM69 reset is active high, matching RadioLib 7.1.2 RF69::reset().
  pinMode(RESET, OUTPUT);
  digitalWrite(RESET, HIGH); delay(1);
  digitalWrite(RESET, LOW); delay(10);
}

void loop() {
  // Accept '?' immediately: usable with raw or line-buffered serial monitors.
  while (Serial.available()) if (Serial.read() == '?') sample();
}
