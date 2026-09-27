# LoRa Trainer for JPTs-Homeassist-Addons

Small SX1262 parameter-training tool for the existing Raspberry Pi / LoRaRF
driver in `LoraMqttBridge/Lora/lora_driver.py`.

## Why master + responder?

LoRa receiver settings must match the transmitter for the test to be meaningful.
The responder therefore receives a CONTROL frame, switches to the requested
SF/BW/CR/TX setting and then answers the requested number of test packets with
the bridge's normal ACK frame. After the last packet it returns to the stable
base RF settings so the next CONTROL frame can be received.

## Start

On the remote Pi:

```bash
cd /app
python3 lora_trainer.py responder --config /config/config.yaml
```

On the local Pi:

```bash
cd /app
python3 lora_trainer.py master \
  --config /config/config.yaml \
  --sf 7 8 9 10 11 12 \
  --bw 125000 250000 500000 \
  --cr 5 6 7 8 \
  --tx 2 6 10 14 18 22 \
  --packets 10 \
  --target 99 \
  --json
```

## Important

This performs real RF transmissions. Keep the antenna connected and stay
within the applicable 868-MHz duty-cycle / power rules for your installation.

The initial implementation optimizes for:
1. target reliability (default 99 %),
2. lower spreading factor,
3. lower TX power.

It reports success rate, RSSI, SNR and RTT. It does not yet claim that a
single short test is statistically sufficient; increase `--packets` for a
more reliable result.

## Integration

Copy `lora_trainer.py` next to the `Lora` package (for example
`/app/lora_trainer.py`). It imports the existing `Lora` modules directly.
