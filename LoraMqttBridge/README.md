# LoRa MQTT Gateway

Home Assistant Add-on: brückt LoRa (SX1262) auf den HA-internen MQTT-Broker.

## Hardware

DX-LR30-900M22S (SX1262), an Raspberry Pi 5 SPI/GPIO — dieselbe Pinbelegung
wie im Schwester-Addon `lora_sx1262`:

| Modul | Raspberry Pi 5    |
| ----- | ----------------- |
| VCC   | Pin 17 / 3.3 V    |
| GND   | Pin 20 / GND      |
| NSS   | Pin 24 / GPIO 8   |
| NRST  | Pin 18 / GPIO 24  |
| MOSI  | Pin 19 / GPIO 10  |
| SCK   | Pin 23 / GPIO 11  |
| MISO  | Pin 21 / GPIO 9   |
| DIO1  | Pin 22 / GPIO 25  |
| BUSY  | Pin 16 / GPIO 23  |
| RXEN  | Pin 15 / GPIO 22  |

## Konfiguration (alles über die HA-UI)

- **log_level**: `debug` / `info` / `normal`
- **mqtt.host/port/user/pass/tls**: leer lassen für HA-internen Broker
- **lora.\***: Frequenz, SF, BW, CR, TX-Power, Sync-Word, TCXO an/aus
- **lora.pins.\***: SPI/GPIO Chip + Pin-Belegung
- **ack.timeout_ms / max_retries / backoff_factor**
- **probe.tx_test**: beim Start einen Test-Frame senden (nur zum Debuggen)
- **topics[]**: Liste `{id, mqtt_topic, direction, qos, retained}`

## Topic-Mapping (LoRa-TopicID ↔ MQTT)

Beide Enden (Pi und HA-Gateway) müssen dieselbe ID-Tabelle kennen.

```yaml
topics:
  - id: 1
    mqtt_topic: "solar/battery/soc"
    direction: bidir
    qos: 1
    retained: true
```

## Start

Nach dem Start durchläuft die App eine 10-stufige Hardware-Probe (siehe Log)
und wechselt danach in den kontinuierlichen RX-Modus. Empfangene MQTT-Frames
werden auf den konfigurierten MQTT-Topics gepublisht; MQTT-Nachrichten auf
TX-Topics werden gebündelt über LoRa gesendet und (bei QoS ≥ 1) mit
ACK-Retransmission zugestellt.

## Home Assistant Integration

Der Add-on nutzt den HA-internen Mosquitto (`services: mqtt:need`). Sensoren
tauchen automatisch als MQTT-Entities auf, sobald die Pi-Seite Werte sendet —
zusätzlich kannst du in HA MQTT Discovery Config-Topics anlegen, um Namen,
Icons und Devices zuzuordnen.


## Raspberry Pi 5 + Home Assistant OS: GPIO and SPI Configuration

When using an SX126x LoRa module with `LoRaRF` inside a Home Assistant OS add-on on a Raspberry Pi 5, two hardware-access issues need to be addressed:

1. GPIO access must use the correct GPIO character device with `rpi-lgpio`.
2. The normal Raspberry Pi header SPI bus (`SPI0`) is disabled by default in Home Assistant OS and must be enabled.

### 1. Enable SPI0 in Home Assistant OS

You can either manually edit the /boot/config.txt oder plug in a specially formatted USB drive, then reboot, see: 
https://developers.home-assistant.io/docs/operating-system/configuration/
(untested, no idea how to exactly apply changes to config.txt)

For manually editing config.txt you need to first enable ssh access to the bare metal system, see
https://developers.home-assistant.io/docs/operating-system/debugging/
They forgot to mention that ha import does nothing else than rebooting. so plug in your USB drive, then reboot.

Log in via
```text
ssh -p 22222 root@homeassistant
```

Home Assistant OS uses a separate boot partition. On the Raspberry Pi 5, the boot partition was mounted at:

```text
/mnt/boot
```

The relevant file is:

```text
/mnt/boot/config.txt
```

By default, it contained:

```text
#dtparam=spi=on
```

Create a backup first:

```sh
cp /mnt/boot/config.txt /mnt/boot/config.txt.bak
```

Then enable SPI:

```sh
sed -i 's/^#dtparam=spi=on$/dtparam=spi=on/' /mnt/boot/config.txt
```

Verify:

```sh
grep -n 'dtparam=spi' /mnt/boot/config.txt
```

Expected:

```text
dtparam=spi=on
```

Reboot:

```sh
systemctl reboot
```

After reboot, SPI0 should be available:

```sh
ls -l /dev/spidev*
```

Expected:

```text
/dev/spidev0.0
/dev/spidev0.1
/dev/spidev10.0
```

Also verify the SPI devices:

```sh
ls -l /sys/bus/spi/devices/spi*
```

Expected:

```text
spi0.0
spi0.1
spi10.0
```

### 2. Use SPI0 for the LoRa module

The LoRa module is physically connected to the standard Raspberry Pi SPI header:

| Signal   | Physical pin | BCM GPIO |
| -------- | -----------: | -------: |
| SCK      |           23 |   GPIO11 |
| MOSI     |           19 |   GPIO10 |
| MISO     |           21 |    GPIO9 |
| NSS / CS |           24 |    GPIO8 |
| RESET    |           18 |   GPIO24 |
| BUSY     |           16 |   GPIO23 |
| DIO1     |           22 |   GPIO25 |
| RXEN     |           15 |   GPIO22 |

Therefore the LoRa configuration must use:

```yaml
spi_bus: 0
spi_cs: 0

reset: 24
busy: 23
dio1: 25

txen: -1
rxen: 22
```

Do **not** use `spi_bus: 10` for the LoRa module.

`/dev/spidev10.0` is a different SPI controller exposed by the Raspberry Pi 5 platform. It is not the SPI controller connected to the standard GPIO header.

### 3. Pass the devices into the Home Assistant add-on

The add-on needs access to both SPI0 and the GPIO character device.

Use:

```yaml
devices:
  - "/dev/spidev0.0:/dev/spidev0.0"
  - "/dev/gpiochip0:/dev/gpiochip0"
```

The important detail is that `/dev/gpiochip0` is the correct GPIO device in this Home Assistant OS environment.

Do not assume that the GPIO chip number shown in sysfs corresponds directly to the physical GPIO controller required by `rpi-lgpio`. On this Raspberry Pi 5 / HAOS setup, attempts to use `/dev/gpiochip10` did not provide the required GPIO access, while `/dev/gpiochip0` did.

### 4. `rpi-lgpio` instead of the original `RPi.GPIO`

`LoRaRF` imports:

```python
import RPi.GPIO
```

directly. To keep the LoRaRF source unchanged while using the modern GPIO implementation, install `rpi-lgpio`.

On Alpine-based Home Assistant OS add-on images, the normal `lgpio` PyPI package is problematic because its published wheels target glibc rather than musl.

The working combination was:

```dockerfile
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    adafruit-lgpio

RUN python3 -m pip install --no-cache-dir --break-system-packages \
    rpi-lgpio --no-deps
```

`adafruit-lgpio` provides the `lgpio` Python module, while `rpi-lgpio` provides the `RPi.GPIO` compatibility layer.

The Raspberry Pi 5 revision also needs to be supplied because the container does not expose the host's device-tree revision information:

```dockerfile
ENV RPI_LGPIO_REVISION=e04171
```

This allows:

```python
import RPi.GPIO
```

to initialize successfully inside the container.

### 5. Required Python packages

The relevant package installation looks like:

```dockerfile
RUN python3 -m pip install --no-cache-dir --break-system-packages \
    adafruit-lgpio

RUN python3 -m pip install --no-cache-dir --break-system-packages \
    rpi-lgpio --no-deps

RUN python3 -m pip install --no-cache-dir --break-system-packages \
    paho-mqtt \
    PyYAML \
    gpiod \
    spidev

RUN python3 -m pip install --no-cache-dir --break-system-packages \
    LoRaRF --no-deps
```

The `--no-deps` on `rpi-lgpio` is intentional: otherwise pip attempts to install the regular `lgpio` package instead of the working Alpine-compatible implementation.

### 6. Final hardware configuration

The resulting setup is:

```text
Raspberry Pi 5
    │
    ├── SPI0
    │    └── /dev/spidev0.0
    │
    └── GPIO
         └── /dev/gpiochip0
              │
              └── rpi-lgpio
                   │
                   └── RPi.GPIO compatibility API
                        │
                        └── LoRaRF / SX126x
```

LoRaRF configuration:

```yaml
spi_bus: 0
spi_cs: 0
reset: 24
busy: 23
dio1: 25
txen: -1
rxen: 22
```

Add-on device passthrough:

```yaml
devices:
  - "/dev/spidev0.0:/dev/spidev0.0"
  - "/dev/gpiochip0:/dev/gpiochip0"
```

This configuration allows the unmodified `LoRaRF` `RPi.GPIO` imports to work through `rpi-lgpio` while using the Raspberry Pi 5's actual GPIO-header SPI0 interface.


