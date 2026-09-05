# cannot install adafruit-lgpio on Python 3.12
FROM ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.22

WORKDIR /app

# needed so it opens correct devices?
ENV RPI_LGPIO_REVISION=e04171

RUN apk add --no-cache \
      python3 \
      py3-pip \
      linux-headers \
      gcc \
      musl-dev \
      swig \
      libgpiod

# this lgpio implementation also delivers the binary which lgpio doesn't
RUN python3 -m pip install --no-cache-dir \
      adafruit-lgpio

# do not install dependencies automatically
RUN python3 -m pip install --no-cache-dir --break-system-packages \
      rpi-lgpio --no-deps

RUN python3 -m pip install --no-cache-dir --break-system-packages \
      paho-mqtt \
      PyYAML \
      gpiod \
      spidev

# do not install dependencies automatically (RPi.GPIO is replaced by lgpio)
RUN python3 -m pip install --no-cache-dir --break-system-packages \
      --no-deps LoRaRF

COPY entry.py /app/entry.py
COPY config.yaml /app/config.yaml
COPY Lora /app/Lora
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
