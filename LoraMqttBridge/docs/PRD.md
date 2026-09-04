# PRD — LoRa MQTT Bridge (Pi Zero 2W ↔ Home Assistant)

## Ursprüngliches Problem
Neues Projekt basierend auf https://github.com/JPT77/Raspi-Lora-Test:
Pi Zero 2W als WiFi-Hotspot + Mosquitto + 4 Python-Aufgaben (Battery-Relay,
MQTT→LoRa, GPIO-Sensoren→LoRa, LoRa→MQTT). Gegenstelle als HA-Add-on in
https://github.com/JPT77/JPTs-Homeassist-Addons. IRQ statt Poll,
konfigurierbar via HA-UI, ACK/Retry, 4-Byte-Frame (V+Flags / Type / Seq / TopicID).

## Architektur-Entscheidungen
- Gemeinsame Codebasis `lora_mqtt_bridge/` für beide Enden (Rolle über Config)
- Vier Pi-Aufgaben laufen als **ein Prozess** (Threads) — teilen sich ein LoRa-Radio
- DIO1-IRQ via **`gpiod` v2** (Fallback SPI-Poll)
- LoRa-Frame: 4-Byte-Header (V/Flags, Type, Seq, TopicID) + Payload; Länge & CRC per SX1262-Hardware
- ACK-Retransmission mit exponential backoff (`timeout_ms`, `max_retries`, `backoff_factor`)
- 10-Schritt-Hardware-Probe beim Boot
- Log-Level: `debug` (inkl. RX-BAD), `info` (RX/TX), `normal` (Fehler + Start/Stop)

## Umgesetzt (Jan 2026)
- [x] Shared Python-Package (`lora_mqtt_bridge/`): protocol, lora_driver, hw_probe, ack_manager, mqtt_client, sensors, bridge, topic_router, config_loader, logger, __main__
- [x] Beide Rollen: `role_pi_node.py` (4 Aufgaben) und `role_ha_gateway.py`
- [x] Pi-Node Deployment: config.yaml, install.sh, hostapd.conf, dnsmasq.conf, mosquitto.conf, systemd-Unit
- [x] HA-Add-on: config.yaml (Options+Schema für HA-UI), Dockerfile, run.sh, src/entry.py, README
- [x] ARCHITECTURE.md mit ASCII-Diagramm; docs/PROTOCOL.md mit Byte-genauer Spec
- [x] Encoder/Decoder-Tests, Config-Loader-Tests: alle grün
- [x] Sensoren: BMP280, AHT20, ADC (MCP3008 & ADS1115) — GPIOs außerhalb BCM 13–24

## Backlog / Nächste Schritte
- P1: Add-on-Icon + `translations/de.yaml` für deutsche HA-UI-Beschriftungen
- P1: MQTT-Discovery-Autopublish (Sensoren erscheinen automatisch in HA)
- P2: Web-Debug-UI im Add-on (letzte RX-Frames, IRQ-Register, RSSI-Historie)
- P2: End-to-End-Testmodus mit zwei simulierten Radios (Loopback statt Hardware)
- P2: Aktualisieren des `LoRaRF` auf `libgpiod`-Fork, sobald verfügbar
- P3: Multi-Node-Support (mehrere Pi-Nodes an ein HA-Gateway; per Node-ID im Payload)

## Test-Status
- Protocol-Encoder/Decoder: 7 Tests grün (siehe `python -c ...` im finish)
- Python-Lint: 0 Fehler
- Hardware-Tests folgen auf echter Ziel-Hardware (Pi Zero 2W + Pi 5 mit SX1262)
