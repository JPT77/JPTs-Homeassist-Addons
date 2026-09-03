#!/usr/bin/env python3

import os
import sys
import time

import spidev
import gpiod
from gpiod.line import Direction, Value


SPI_DEVICE = os.environ.get("SPI_DEVICE", "/dev/spidev10.0")
GPIO_DEVICE = os.environ.get("GPIO_DEVICE", "/dev/gpiochip10")

# Raspberry Pi GPIO numbering / line offsets
#GPIO_DIO2 = 1
GPIO_RXEN = 22
GPIO_BUSY = 23
GPIO_NSS = 8
GPIO_NRST = 24
GPIO_DIO1 = 25

def check_device(path: str) -> None:
    print(f"DEVICE {path}: ", end="")

    if os.path.exists(path):
        print("OK")
    else:
        print("MISSING")
        raise RuntimeError(f"Device not found: {path}")


def test_spi() -> None:
    print()
    print("----------------------------------------")
    print("SPI TEST")
    print("----------------------------------------")

    spi = spidev.SpiDev()

    # /dev/spidev10.0 = bus 10, chip-select 0
    spi.open(10, 0)

    spi.max_speed_hz = 1_000_000
    spi.mode = 0
    spi.bits_per_word = 8

    print(f"SPI device      : {SPI_DEVICE}")
    print(f"SPI mode        : {spi.mode}")
    print(f"SPI speed       : {spi.max_speed_hz}")
    print(f"SPI bits        : {spi.bits_per_word}")

    # Dummy transfer.
    #
    # Der SX1262 antwortet auf diesen Transfer noch nicht sinnvoll.
    # Wir prüfen hier zunächst ausschließlich, ob der Linux-SPI
    # Controller geöffnet und angesprochen werden kann.
    tx = [0x00, 0x00]
    rx = spi.xfer2(tx)

    print(f"SPI TX          : {[hex(x) for x in tx]}")
    print(f"SPI RX          : {[hex(x) for x in rx]}")
    print("SPI transfer    : OK")

    spi.close()


def test_gpio() -> None:
    print()
    print("----------------------------------------")
    print("GPIO TEST")
    print("----------------------------------------")

    print(f"GPIO device     : {GPIO_DEVICE}")

    chip = gpiod.Chip(GPIO_DEVICE)

    print(f"GPIO chip       : {chip.get_info().name}")
    print(f"GPIO label      : {chip.get_info().label}")
    print(f"GPIO lines      : {chip.get_info().num_lines}")

    # Wir lesen zunächst BUSY und DIO1.
    #
    # Beide Leitungen sind Ausgänge des SX1262 und dürfen deshalb
    # NICHT als Ausgang vom Raspberry Pi konfiguriert werden.

    with gpiod.Chip(GPIO_CHIP) as chip:
        request = chip.request_lines(
            consumer="lora-sx1262-test",
            config={
                GPIO_BUSY: gpiod.LineSettings(
                    direction=Direction.INPUT
                ),
                GPIO_DIO1: gpiod.LineSettings(
                    direction=Direction.INPUT
                ),
            },
        )

        busy = request.get_value(GPIO_BUSY)
        dio1 = request.get_value(GPIO_DIO1)

        print(f"BUSY: {busy}")
        print(f"DIO1: {dio1}")

    print(f"GPIO{GPIO_BUSY:02d} BUSY   : {busy.get_value()}")
    print(f"GPIO{GPIO_DIO1:02d} DIO1   : {dio1.get_value()}")

    dio1.release()
    busy.release()

    chip.close()

    print("GPIO input test : OK")


def main() -> int:
    print("LoRa SX1262 Raspberry Pi 5 hardware test")
    print()

    try:
        check_device(SPI_DEVICE)
        check_device(GPIO_DEVICE)

        test_spi()
        test_gpio()

    except Exception as exc:
        print()
        print("ERROR")
        print("-----")
        print(type(exc).__name__ + ": " + str(exc))
        return 1

    print()
    print("========================================")
    print(" Hardware access test successful")
    print("========================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
