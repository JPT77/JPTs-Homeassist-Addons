ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.22
FROM ${BUILD_FROM}

WORKDIR /app

RUN apk add --no-cache \
    python3 \
    py3-pip \
    linux-headers \
    gcc \
    musl-dev

RUN python3 -m pip install --no-cache-dir --break-system-packages \
    spidev \
    gpiod

COPY src /app
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
