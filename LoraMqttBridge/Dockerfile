ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.22
FROM ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.22

WORKDIR /app

RUN python3 --version

RUN apk add --no-cache \
      python3 \
      python3-dev \
      py3-pip \
      linux-headers \
      swig \
      gcc \
      musl-dev \
      make \
      git

RUN git clone --depth 1 https://github.com/joan2937/lg.git /tmp/lg \
    && cd /tmp/lg \
    && make \
    && make install

RUN python3 -m pip install --no-cache-dir --break-system-packages \
      rpi-lgpio \
      LoRaRF \
      paho-mqtt \
      PyYAML \
      gpiod \
      spidev

RUN python3 -m pip install --no-cache-dir --break-system-packages \
      --no-deps LoRaRF

COPY entry.py /app/entry.py
COPY config.yaml /app/config.yaml
COPY Lora /app/Lora
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
