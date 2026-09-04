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

COPY entry.py /app/entry.py
COPY config.yaml /app/config.yaml
COPY LoraMqttBridge /app/LoraMqttBridge
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
