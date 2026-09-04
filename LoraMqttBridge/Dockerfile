ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.22
FROM ${BUILD_FROM}

WORKDIR /app

RUN apk add --no-cache \
      python3 \
      py3-pip \
      linux-headers \
      gcc \
      musl-dev \
      libgpiod

RUN python3 -m pip install --no-cache-dir --break-system-packages \
      LoRaRF \
      paho-mqtt \
      PyYAML \
      gpiod \
      spidev

# Der geteilte Code liegt eine Ebene über dem Addon; wir kopieren ihn ins Image
COPY src /app/src
COPY lora_mqtt_bridge /app/lora_mqtt_bridge
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
