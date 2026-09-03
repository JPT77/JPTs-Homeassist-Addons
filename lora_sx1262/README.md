# LoRa SX1262

Home Assistant App for testing an SX1262 LoRa module directly
connected to the Raspberry Pi 5 SPI/GPIO interface.

## Hardware

DX-LR30-900M22S:

| Module | Raspberry Pi |
|---|---|
| VCC | Pin 17 / 3.3 V |
| GND | Pin 20 / GND |
| NSS | Pin 24 / GPIO8 / CE0 |
| NRST | Pin 18 / GPIO24 |
| MOSI | Pin 19 / GPIO10 |
| SCK | Pin 23 / GPIO11 |
| DIO1 | Pin 22 / GPIO25 |
| MISO | Pin 21 / GPIO9 |
| DIO2 | -- |
| BUSY | Pin 16 / GPIO23 |
| RXEN | Pin 15 / GPIO22 |

## Linux devices

The current Raspberry Pi 5 / Home Assistant OS exposes:

- `/dev/spidev10.0`
- `/dev/gpiochip10`

The application currently performs only a hardware-access test.

No LoRa transmission is performed.

## Current tests

1. SPI device exists
2. SPI device can be opened
3. SPI transfer can be performed
4. GPIO chip can be opened
5. BUSY can be read
6. DIO1 can be read

The next development step is an actual SX1262 command/status test.
