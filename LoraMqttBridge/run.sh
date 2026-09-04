#!/usr/bin/with-contenv bashio

set -e

echo "========================================="
echo "LoRa MQTT Gateway (Home Assistant Add-on)"
echo "========================================="

echo "=== GPIO Python Diagnose ==="
python3 -m pip show -f rpi-lgpio || true
python3 -c '
import importlib.util;
print("RPi:", importlib.util.find_spec("RPi"));
print("RPi.GPIO:", importlib.util.find_spec("RPi.GPIO"))
' || true

ls -la /proc/device-tree || true
ls -la /proc/device-tree/system 2>&1 || true
ls -la /dev/gpiochip* 2>&1 || true
ls -la /dev/spidev* 2>&1 || true

python3 - <<'PY'
import sys
import RPi.GPIO as GPIO
import lgpio

print("Python:", sys.version)
print("RPi.GPIO:", GPIO.__file__)
print("GPIO VERSION:", getattr(GPIO, "VERSION", "?"))
print("lgpio:", lgpio.__file__)
print("lgpio version:", getattr(lgpio, "VERSION", "?"))

print()
print("TEST: GPIO import OK")
PY

python3 -c "import lgpio; print(lgpio.__file__)" ||true
python3 -c "import RPi.GPIO as GPIO; print(GPIO.__file__); print(GPIO.VERSION)" ||true
python3 -c "from LoRaRF import SX126x; print('LoRaRF + RPi.GPIO import OK')"||true

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
