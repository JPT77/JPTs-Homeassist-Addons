# Architektur: LoRa ↔ MQTT Bridge

Dieses Projekt besteht aus **zwei physikalischen Enden** und einer **gemeinsamen Python-Code-Basis**:

```
                                   ┌─────────────────────────────────┐
                                   │  Raspberry Pi Zero 2W  (Feld)   │
                                   │  ─────────────────────────────  │
                                   │                                 │
   Wi-Fi Clients ── Wi-Fi AP ──▶   │  hostapd + dnsmasq              │
   (Akku-BMS,       (Hotspot)      │      │                          │
    Sensornodes,                   │      ▼                          │
    ESPHome, …)                    │  Mosquitto (MQTT-Broker)        │
                                   │      ▲                          │
                                   │      │ paho-mqtt                │
                                   │  ┌───┴────────────────────────┐ │
                                   │  │  lora_mqtt_bridge (Python) │ │
                                   │  │  role = pi_node            │ │
                                   │  │  ──────────────────────    │ │
                                   │  │  · battery_relay           │ │
                                   │  │  · mqtt→lora forwarder     │ │
                                   │  │  · gpio sensor publisher   │ │
                                   │  │  · lora→mqtt forwarder     │ │
                                   │  └───┬────────────────────────┘ │
                                   │      │ SPI + GPIO (IRQ DIO1)    │
                                   │      ▼                          │
                                   │  SX1262 (DX-LR30-900M22S)       │
                                   └──────────────┼──────────────────┘
                                                  │
                                        868 MHz LoRa  (custom framing)
                                                  │
                                   ┌──────────────┼──────────────────┐
                                   │  Home Assistant OS (RPi 5)      │
                                   │      │                          │
                                   │      ▼                          │
                                   │  SX1262 an SPI/GPIO             │
                                   │      ▲                          │
                                   │  ┌───┴────────────────────────┐ │
                                   │  │  lora_mqtt_bridge (Python) │ │
                                   │  │  role = ha_gateway         │ │
                                   │  │  (dieselbe Codebasis)      │ │
                                   │  └───┬────────────────────────┘ │
                                   │      │ paho-mqtt                │
                                   │      ▼                          │
                                   │  HA-interner Mosquitto          │
                                   │  → Home Assistant Entities      │
                                   └─────────────────────────────────┘
```

## 1. Gemeinsame Code-Basis (`lora_mqtt_bridge/`)

Ja, ein **shared package** ist sinnvoll. Beide Enden reden dasselbe LoRa-Frameprotokoll,
brauchen denselben ACK/Retry-Mechanismus und dieselbe MQTT↔LoRa Topic-Übersetzung.
Nur die *Rolle* unterscheidet sich (via `role: pi_node` / `role: ha_gateway` in der
Config bzw. HA-Add-on Options).

Module:

| Modul              | Zweck                                                                 |
| ------------------ | --------------------------------------------------------------------- |
| `protocol.py`      | 4-Byte-Header Encoding/Decoding, Flags, Typen, Topic-IDs              |
| `lora_driver.py`   | SX1262/SX126x Treiber-Wrapper; **IRQ-getrieben** via DIO1             |
| `hw_probe.py`      | Schrittweises Erforschen der LoRa-Hardware beim Startup               |
| `ack_manager.py`   | ACK-Erwartung, Retransmission mit exponential backoff, Sequenznummern |
| `mqtt_client.py`   | paho-mqtt Wrapper mit Auto-Reconnect, TLS, LWT                        |
| `sensors.py`       | I²C (BMP280, AHT20) + ADC-Wasserstand (MCP3008/ADS1115)               |
| `topic_router.py`  | Übersetzung MQTT-Topic ↔ Topic-ID                                     |
| `logger.py`        | Log-Level (`debug` / `info` / `normal`)                               |
| `config_loader.py` | YAML aus Datei ODER HA-Options einlesen                               |
| `roles/`           | Entry-Points je Rolle                                                 |
| ├─ `pi_node.py`    | Startet alle vier Pi-Aufgaben parallel (asyncio)                      |
| └─ `ha_gateway.py` | LoRa ↔ HA-MQTT-Broker                                                 |
| `app.py`           | `main()` – wählt Rolle anhand Config                                  |

Alle vier Pi-Aufgaben laufen als **asyncio-Tasks in einem Prozess**, teilen sich
Broker-Verbindung, LoRa-Radio und Sequenznummern. Das ist einfacher als vier
Prozesse mit systemd, spart Ressourcen auf dem Zero 2W und vermeidet Konflikte
um das eine SPI/GPIO-Radio.

## 2. Raspberry Pi Zero 2W (`pi_node/`)

- **OS:** Raspberry Pi OS Bookworm (64-bit) — auf Zero 2W lauffähig.
- **Wi-Fi-Hotspot:** `hostapd` (2,4 GHz) + `dnsmasq` (DHCP+DNS) auf `wlan0`.
  Ethernet gibt es am Zero 2W nicht; falls Backhaul nötig → USB-LAN oder eth-USB-Dongle.
- **MQTT-Broker:** `mosquitto` (lokal auf 1883), erreichbar über die Hotspot-IP
  (z. B. `192.168.50.1`). Optional Auth via `mosquitto_passwd`.
- **Bridge-Prozess:** Ein einziger `systemd`-Service (`lora-bridge.service`),
  der `python3 -m lora_mqtt_bridge --config /etc/lora-bridge/config.yaml`
  startet und die vier Aufgaben als asyncio-Tasks fährt.

## 3. LoRa-Framing (siehe `docs/PROTOCOL.md`)

```
Byte 0:  VVVFFFFF     V=Version(3b)  F=Flags(5b: ACK-REQ, ACK-RESP, RETRY, RSV, RSV)
Byte 1:  TT            Type (MQTT=0x01, CONTROL=0x02, ACK=0x03, HELLO=0x04)
Byte 2:  SS            Sequence 0..255
Byte 3:  II            Topic-ID (Mapping in Config)
Byte 4..N: Payload    (UTF-8 MQTT-Payload oder Control-Struktur)
```

- **Länge & CRC** kommen kostenlos aus dem SX1262-LoRa-Header (explicit header + HW-CRC).
- **ACK-Handling:** Sender setzt ACK-REQ, wartet auf Frame mit Type=ACK und derselben
  Sequenznummer. Timeout → RETRY-Flag setzen und erneut senden. Parameter konfigurierbar
  (`ack_timeout_ms`, `max_retries`, `retry_backoff`).
- **Home Assistant Kompatibilität:** Die Gegenstelle im HA-Addon spiegelt LoRa-Pakete
  auf ihren eigenen MQTT-Broker; von dort greift HA über MQTT Discovery zu. Damit ist
  jede HA-App/Integration, die MQTT versteht, kompatibel — kein proprietäres HA-API
  nötig.

## 4. Konfiguration

**Pi-Seite:** `/etc/lora-bridge/config.yaml` (deployed durch `install.sh`).

**HA-Seite:** Alles via **HA-Add-on Options** in der HA-UI. `run.sh` schreibt
die Options via `bashio` in eine YAML, die `config_loader.py` liest. So sind
Frequenz, SF, Sync-Word, MQTT-Credentials, Topic-Mappings und Log-Level direkt
in der HA-UI editierbar.

Konfigurierbar über Config/HA-UI:

- `log_level`: `debug` (inkl. fehlerhafter Pakete), `info` (RX/TX-Pakete), `normal` (nur Fehler & Start/Stop)
- `mqtt.host`, `mqtt.port`, `mqtt.username`, `mqtt.password`, `mqtt.tls`, `mqtt.client_id`
- `lora.frequency_hz`, `lora.spreading_factor`, `lora.bandwidth_hz`, `lora.coding_rate`,
  `lora.tx_power_dbm`, `lora.sync_word`, `lora.chip` (`sx1261`/`sx1262`), `lora.use_tcxo`
- `lora.pins.*` (BUSY, RESET, DIO1, RXEN, SPI-Bus, CS, GPIO-Chip)
- `ack.timeout_ms`, `ack.max_retries`, `ack.backoff_factor`
- `topics[]`: Liste `{id: int, mqtt_topic: str, direction: rx|tx|bidir, qos: 0/1/2, retained: bool}`
- `battery_relay.sources`: 2 Topics, deren jede Änderung an `battery_relay.target` weitergeleitet wird
- `sensors[]`: Sensor-Definitionen (BMP280 i2c-addr, AHT20 i2c-addr, ADC-channel/gain, Poll-Intervall, Topic-ID)

## 5. IRQ-basiertes RX/TX

Statt Busy-Polling registriert `lora_driver.py` **DIO1 als GPIO-Interrupt**
(via `gpiod` v2 auf HA-OS / `rpi-lgpio` auf Raspberry Pi OS). RX_DONE, CRC_ERR,
HEADER_ERR und TIMEOUT werden im IRQ-Handler an eine `asyncio.Queue` gemeldet,
den die Empfangs-Task konsumiert. Wenn DIO1 nicht verfügbar ist, fällt der
Treiber automatisch auf einen 20-ms-SPI-Poll-Loop zurück (Kompatibilität mit
dem existierenden `Raspi-Lora-Test`).

## 6. Hardware-Probe beim Boot

`hw_probe.py` läuft schrittweise durch:

1. SPI-Device öffnen (`/dev/spidev*.*`)
2. GPIO-Chip öffnen (`/dev/gpiochip*`)
3. BUSY-Level lesen (muss LOW werden können)
4. NRST toggeln → BUSY sollte kurz HIGH gehen
5. SX126x `GetStatus` per SPI → sinnvoller Chip-Modus?
6. `GetDeviceErrors` → keine Fehler
7. TCXO/XOSC-Setup (falls konfiguriert)
8. Modulations- & Paket-Parameter setzen
9. Sync-Word setzen
10. Kurzer TX-Test (nur wenn `probe.tx_test: true`)

Jeder Schritt loggt Erfolg/Fehler mit klarer Meldung; bei Fehler bricht die
App mit Exit-Code ab, damit systemd/HA-Supervisor sie neu startet.

## 7. Sensoren am GPIO (Pi-Seite)

- **BMP280** (I²C, Adresse `0x77`) → Druck + Temperatur
- **AHT20** (I²C, Adresse `0x38`) → Luftfeuchte + Temperatur
- **Wasserstand-ADC** (MCP3008 SPI **oder** ADS1115 I²C) → Analogwert
  (der Pi hat keinen ADC an Bord; das Original-ESPHome-Config nutzt `GPIO36`,
  was ein ESP32-ADC-Kanal ist).

Alle Sensoren werden periodisch (`sensors[i].poll_interval_s`) gelesen und
mit dem konfigurierten Topic (bzw. Topic-ID über LoRa) publiziert.

**GPIO-Belegung:** Wir halten uns strikt an die Pi-Zero-2W-Pinbelegung des
Proof-of-Concept (BCM 22/23/24/25 für RXEN/BUSY/NRST/DIO1). Alle
Sensor-Chip-Select-Pins liegen **außerhalb von BCM 13–24**, wie gewünscht
(freigehalten: I²C = BCM 2/3, MCP3008-CS = BCM 7, ADS1115 nutzt I²C ohnehin).

## 8. Deployment

**Pi Zero 2W:**
```bash
git clone https://github.com/JPT77/JPTs-Homeassist-Addons.git
cd JPTs-Homeassist-Addons/pi_node
sudo ./setup/install.sh
```

**Home Assistant:**
- Repository in HA hinzufügen: `https://github.com/JPT77/JPTs-Homeassist-Addons`
- Add-on **"LoRa MQTT Gateway"** installieren, Optionen in der UI konfigurieren, starten.
