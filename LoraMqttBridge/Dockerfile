FROM ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.22

WORKDIR /app

# python3-dev make git
RUN apk add --no-cache \
      python3 \
      py3-pip \
      linux-headers \
      gcc \
      musl-dev \
      swig \
      libgpiod

RUN python3 -m pip install --no-cache-dir \
    adafruit-lgpio

RUN python3 -m pip install --no-cache-dir --break-system-packages \
    rpi-lgpio --no-deps

RUN python3 -c "import lgpio; print('lgpio OK:', lgpio.__file__)"

RUN python3 -m pip show rpi-lgpio RPi.GPIO LoRaRF || true

RUN python3 -m pip install --no-cache-dir --break-system-packages \
      paho-mqtt \
      PyYAML \
      gpiod \
      spidev

RUN python3 -m pip show rpi-lgpio RPi.GPIO LoRaRF || true

RUN python3 -m pip install --no-cache-dir --break-system-packages \
      --no-deps LoRaRF

RUN python3 -m pip show rpi-lgpio RPi.GPIO LoRaRF || true

# add  a dummy ldconfig that does nothing.
#RUN printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/ldconfig \
#    && chmod +x /usr/local/bin/ldconfig \
#    && git clone --depth 1 https://github.com/joan2937/lg.git /tmp/lg \
#    && cd /tmp/lg \
#    && make \
#    && make install \
#    && rm -rf /tmp/lg \
#    && rm -f /usr/local/bin/ldconfig

COPY entry.py /app/entry.py
COPY config.yaml /app/config.yaml
COPY Lora /app/Lora
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
