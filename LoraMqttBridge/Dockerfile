ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.22
FROM ${BUILD_FROM}

WORKDIR /app

RUN apk add --no-cache \
      python3 \
      python3-dev \
      py3-pip \
      linux-headers \
      swig \
      gcc \
      musl-dev \
      libgpiod

RUN python3 -m pip install --no-cache-dir --break-system-packages \
      rpi-lgpio \
      LoRaRF \
      paho-mqtt \
      PyYAML \
      gpiod \
      spidev

COPY entry.py /app/entry.py
COPY config.yaml /app/config.yaml
COPY Lora /app/Lora
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
