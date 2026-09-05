#!/usr/bin/with-contenv bashio

set -e

echo "========================================="
echo "LoRa MQTT Gateway (Home Assistant Add-on)"
echo "========================================="

echo "=== DEBUGGING in RUN.SH ==="

echo "SPIDEV"
ls -l /dev/spidev10.0

python3 - <<'PY'
import spidev

spi = spidev.SpiDev()
spi.open(10, 0)
spi.max_speed_hz = 125000
spi.mode = 0

for i in range(5):
    r = spi.xfer2([0xC0, 0x00, 0x00])
    print(i, [f"0x{x:02X}" for x in r])

spi.close()
PY

echo "============================"

# HA-Optionen liegen in /data/options.json — config_loader.py liest das
# automatisch. Wir setzen zusätzlich die Rolle fest.
export LORA_BRIDGE_ROLE=ha_gateway

# HA-eigener Mosquitto: wenn kein Host gesetzt, aus HA-Services holen.
if bashio::services.available "mqtt"; then
  MQTT_HOST=$(bashio::services "mqtt" "host")
  MQTT_PORT=$(bashio::services "mqtt" "port")
  MQTT_USER=$(bashio::services "mqtt" "username")
  MQTT_PASS=$(bashio::services "mqtt" "password")
  echo "MQTT (HA service): ${MQTT_HOST}:${MQTT_PORT} user=${MQTT_USER}"
  export LORA_BRIDGE_MQTT_HOST="${MQTT_HOST}"
  export LORA_BRIDGE_MQTT_PORT="${MQTT_PORT}"
  export LORA_BRIDGE_MQTT_USER="${MQTT_USER}"
  export LORA_BRIDGE_MQTT_PASS="${MQTT_PASS}"
fi

# In den Add-on-Optionen ist "role" nicht enthalten (fest ha_gateway).
# config_loader.py mergt /data/options.json direkt in Config, wir überschreiben
# die Rolle über Env.
python3 /app/entry.py
