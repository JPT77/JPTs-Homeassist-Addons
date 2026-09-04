# Raspberry Pi Zero 2W — LoRa-MQTT-Bridge

Der Pi-Node läuft als **ein systemd-Service** (`lora-bridge.service`), der
alle vier vom Problem geforderten Aufgaben in einem Prozess (asyncio/threads)
kombiniert und dabei ein einziges LoRa-Radio konfliktfrei nutzt.

## Installation

```bash
git clone https://github.com/JPT77/JPTs-Homeassist-Addons.git
cd JPTs-Homeassist-Addons/pi_node
sudo ./setup/install.sh
sudo nano /etc/lora-bridge/config.yaml
sudo reboot
```

Nach dem Reboot:

- Wi-Fi-Hotspot **`lora-bridge`** ist aktiv (2.4 GHz, WPA2, PSK aus
  `hostapd.conf`)
- Mosquitto lauscht auf `192.168.50.1:1883`
- Bridge läuft: `sudo systemctl status lora-bridge`
- Live-Log: `sudo journalctl -u lora-bridge -f`

## Aufgaben (alle konfigurierbar)

1. **Battery-Relay:** `battery_relay.sources` → `battery_relay.target`
   (jede Änderung wird sofort gepublisht).
2. **MQTT→LoRa Forwarder:** jedes Topic in `topics[]` mit `direction: tx`
   oder `bidir` wird auf dem WiFi-Broker subscribed und via LoRa gesendet.
3. **GPIO-Sensor-Publisher:** Sensoren in `sensors[]` werden periodisch
   ausgelesen und (a) lokal auf WiFi-MQTT gepublisht, (b) mit `topic_id`
   über LoRa an das HA-Gateway geschickt.
4. **LoRa→MQTT Forwarder:** jedes Topic mit `direction: rx` oder `bidir`
   spiegelt LoRa-Empfänge zurück in den WiFi-Broker.

## Hardware

- Raspberry Pi Zero 2W (Raspberry Pi OS Bookworm 64-bit)
- SX1262-Modul DX-LR30-900M22S — Pinbelegung siehe `config.yaml → lora.pins`
- I²C-Sensoren: BMP280 (0x77), AHT20 (0x38)
- ADC für Wasserstand: **ADS1115** (I²C 0x48) oder **MCP3008** (SPI, CS auf
  `/dev/spidev0.1`)

Alle vorbelegten Pins liegen **außerhalb** BCM 13–24 (bis auf die 22–25, die
das LoRa-Modul selbst benutzt).

## Log-Level umschalten

```yaml
log_level: debug   # zeigt auch fehlerhaft empfangene Pakete
log_level: info    # jeder erfolgreiche RX/TX (Standard)
log_level: normal  # nur Fehler + Startup/Shutdown
```

Nach Änderung: `sudo systemctl restart lora-bridge`.
