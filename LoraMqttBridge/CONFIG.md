# LoRa <-> MQTT Bridge Configuration & Forwarding Specification

This document details the configuration architecture for the LoRa <-> MQTT Gateway and Pi Node, including the single source of truth
for topics, binary payload packing, and local MQTT subscriptions using JSON queries.

---

## 1. Architecture Overview

The system divides configuration into two distinct layers:

1. **Shared Topic & Field Definitions (`topics.yaml`)**:
- Acts as the single source of truth shared between both endpoints (Home Assistant Gateway and Raspberry Pi Field Node).
- Eliminates redundant configuration.
- Defines the LoRa 8-bit Topic IDs, MQTT topics, QoS, retained flags, and binary data types for compact over-the-air transmission.

2. **Instance-Specific MQTT Subscriptions (`mqtt_subscriptions`)**:
- Configured independently on each node (`config.yaml` for HA Gateway, `PiNode/config.yaml` for the Pi Node).
- Targets only the local MQTT broker that the respective instance connects to.
- Allows subscribing to local MQTT topics, extracting values via dot-notation JSON queries, and forwarding them either over LoRa
or to another local MQTT topic.

---

## 2. Shared Topics & Field Definitions (`topics.yaml`)

Over-the-air LoRa bandwidth is strictly limited. Rather than sending verbose JSON strings across the air, message values are packed
into compact binary representations based on configured field types. When receiving a frame, the bridge unpacks the binary bytes and
reconstructs the MQTT JSON payload using the configured field names.

### 2.1 Supported Field Types

| Type Name | Binary Format | Size | Description |
| :--- | :--- | :--- | :--- |
| `float` / `float32` | `<f` (IEEE 754) | 4 Bytes | Single-precision floating point |
| `double` / `float64`| `<d` (IEEE 754) | 8 Bytes | Double-precision floating point |
| `int8` | `<b` | 1 Byte | Signed 8-bit integer (-128 to 127) |
| `uint8` / `byte` | `<B` | 1 Byte | Unsigned 8-bit integer (0 to 255) |
| `int16` | `<h` | 2 Bytes | Signed 16-bit integer |
| `uint16` | `<H` | 2 Bytes | Unsigned 16-bit integer |
| `int` / `int32` | `<i` | 4 Bytes | Signed 32-bit integer |
| `uint` / `uint32` | `<I` | 4 Bytes | Unsigned 32-bit integer |
| `int64` | `<q` | 8 Bytes | Signed 64-bit integer |
| `uint64` | `<Q` | 8 Bytes | Unsigned 64-bit integer |
| `bool` | `<?` | 1 Byte | Boolean (true/false) |
| `str` / `string` | UTF-8 bytes | Variable | String (length-prefixed if preceding other fields) |

### 2.2 Example `topics.yaml`

```yaml
topics:
- id: 1
    mqtt_topic: "solar/battery/soc"
    direction: bidir
    qos: 1
    retained: true
    fields:
    - name: soc
        type: float

- id: 2
    mqtt_topic: "sensors/keller/pressure"
    direction: bidir
