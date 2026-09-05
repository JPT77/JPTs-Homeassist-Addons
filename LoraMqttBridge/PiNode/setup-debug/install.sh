#!/bin/sh

if [ "$(basename "$PWD")" = "setup-debug" ]; then
    echo "Must be called as 'source PiNode/setup-debug/install.sh' from LoraMqttBridge"
    return 1
fi

if [ "$(basename "$PWD")" != "LoraMqttBridge" ]; then
    echo "Must be called as 'source PiNode/setup-debug/install.sh' from LoraMqttBridge"
    return 1
fi

python -m venv .
. bin/activate
pip install LoRaRF paho-mqtt PyYAML smbus2 spidev gpiod

echo "Start with python -m Lora --config PiNode/config.yaml"
