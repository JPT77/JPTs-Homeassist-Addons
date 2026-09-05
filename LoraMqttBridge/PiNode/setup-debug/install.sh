#!/bin/sh
if [[ "$(basename "$PWD")" == "setup-debug" ]]; then
    echo "Must be called from LoraMqttBridge"
    exit 1
fi
python -m venv
source bin/activate
pip install LoRaRF paho-mqtt PyYAML smbus2 spidev gpiod

echo "Start with python -m Lora --config PiNode/config.yaml"
