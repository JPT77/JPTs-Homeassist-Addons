# LoRa MQTT Bridge — Pi Zero 2W ↔ Home Assistant

Ein neues Projekt basierend auf dem Proof-of-Concept
[Raspi-Lora-Test](https://github.com/JPT77/Raspi-Lora-Test).

Beide Enden — der **Raspberry Pi Zero 2W** im Feld und das
**Home Assistant Add-on** auf dem Pi 5 — teilen sich **dieselbe Codebasis**
(`lora_mqtt_bridge/`). Sie unterscheiden sich nur in der `role`, der
MQTT-Config und den GPIO/SPI-Devicepfaden.

## Struktur

```
├── lora_mqtt_bridge/        ← gemeinsames Python-Package
│   ├── protocol.py          ← 4-Byte-Header Frame-Encoding
│   ├── lora_driver.py       ← SX126x-Wrapper, DIO1-IRQ via gpiod v2
│   ├── hw_probe.py          ← 10-Schritt-Startup-Erforschung der Hardware
│   ├── ack_manager.py       ← ACK + Retransmission + Backoff
│   ├── mqtt_client.py       ← paho-mqtt Auto-Reconnect + LWT
│   ├── sensors.py           ← BMP280, AHT20, MCP3008, ADS1115
│   ├── bridge.py            ← MQTT ↔ LoRa Kern
│   ├── topic_router.py
│   ├── config_loader.py     ← YAML + /data/options.json
│   ├── logger.py            ← Log-Levels debug/info/normal
│   ├── role_pi_node.py      ← Entry für den Pi Zero 2W
│   ├── role_ha_gateway.py   ← Entry für das HA-Addon
│   └── __main__.py          ← `python -m lora_mqtt_bridge`
│
├── pi_node/                 ← Deployment für den Pi Zero 2W
│   ├── config.yaml
│   ├── requirements.txt
│   ├── setup/install.sh     ← hostapd + dnsmasq + mosquitto + systemd
│   └── README.md
│
├── ha_addon/lora_mqtt_gateway/
│   ├── config.yaml          ← HA-Add-on Manifest (alle Optionen in HA-UI)
│   ├── Dockerfile
│   ├── run.sh
│   ├── src/entry.py
│   └── README.md
│
├── ARCHITECTURE.md          ← ausführliche Architektur + ASCII-Diagramm
└── docs/PROTOCOL.md         ← Detailspezifikation des LoRa-Frames
```

## Kurzantworten zu deinen Rückfragen

- **Gemeinsame Codebasis:** Ja, sinnvoll — beide Enden reden dasselbe
  Protokoll, brauchen ACK/Retry, Topic-Routing und Config. Der Unterschied
  sind nur Devicepfade und Zusatz-Tasks (Sensoren + Battery-Relay laufen nur
  am Pi).
- **Länge & CRC im Frame:** korrekt weggelassen — der SX1262 liefert beides
  in Hardware (explicit header + HW-CRC).
- **IRQ statt Poll:** DIO1 wird über `gpiod` v2 als Edge-Interrupt
  angehängt. Fallback auf SPI-Poll ist nur, wenn `use_irq: false` oder
  `gpiod` fehlt — das entspricht deinem Wunsch.
- **HA-App:** Alle Config-Werte liegen in `ha_addon/lora_mqtt_gateway/config.yaml`
  unter `options:` und werden in der HA-UI editierbar (Schema-basiert).
- **Log-Level, MQTT-Einstellungen, ACK-Parameter, Sync-Word, Frequenz, TCXO,
  Topic-Mapping:** alles konfigurierbar (Pi-YAML **und** HA-Options).

## Nächste Schritte für dich

1. Repo-Struktur in dein GitHub-Repo `JPTs-Homeassist-Addons` übernehmen:
   - `lora_mqtt_bridge/` neben `lora_sx1262/`
   - `ha_addon/lora_mqtt_gateway/` als neuer Add-on-Ordner
   - `pi_node/` in einem Sub-Repo oder Ordner
2. Auf dem Pi Zero 2W: `sudo ./pi_node/setup/install.sh`
3. In HA: Repo hinzufügen → **LoRa MQTT Gateway** installieren → Optionen
   in der UI setzen → Add-on starten.
