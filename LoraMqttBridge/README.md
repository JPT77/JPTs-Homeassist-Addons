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
