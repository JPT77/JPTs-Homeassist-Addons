ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.22
FROM ${BUILD_FROM}

WORKDIR /app

RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-spidev \
    py3-gpiod

COPY src /app
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD ["/run.sh"]
