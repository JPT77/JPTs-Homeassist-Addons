# dataconvert.jq – jq helper functions for LoRa binary payload encoding.
# Prepended to every jq expression at runtime (see mqtt_forwarder.py).
#
# Sentinel values for invalid / overflow data:
#   uint8  → 255   (0xFF)
#   uint16 → 65535 (0xFFFF)
#   uint32 → 4294967295 (0xFFFFFFFF)
#   int8   → 128   (0x80, maps to -128 as two's complement)
#   int16  → 32768 (0x8000)
# NOTE: jq does not support hex literals (0xFF etc.) – use decimal values.

# --- Unsigned integer clamping ------------------------------------------------

def u8:
    if . == null or . < 0 or . > 254       then 255       else . | floor end;

def u16:
    if . == null or . < 0 or . > 65534     then 65535     else . | floor end;

def u32:
    if . == null or . < 0 or . > 4294967294 then 4294967295 else . | floor end;

# --- Signed integer clamping (two's complement, sentinel = most-negative) ----

def i8:
    if . == null or . < -127 or . > 127
    then 128
    else
        if . < 0 then 256 + (. | floor) else . | floor end
    end;

def i16:
    if . == null or . < -32767 or . > 32767
    then 32768
    else
        if . < 0 then 65536 + (. | floor) else . | floor end
    end;

def i32:
    if . == null or . < -2147483647 or . > 2147483647
    then 2147483648
    else
        if . < 0 then 4294967296 + (. | floor) else . | floor end
    end;

# --- Big-endian byte array helpers --------------------------------------------

def be16:
    u16 |
    [
        (. / 256 | floor),
        (. % 256)
    ];

def be32:
    u32 |
    [
        (. / 16777216 | floor),
        ((. / 65536   | floor) % 256),
        ((. / 256     | floor) % 256),
        (. % 256)
    ];

# --- Duration string parser ---------------------------------------------------
# Parses Tasmota Wifi.Downtime format: "0T00:01:09" → total seconds (integer)
# Returns null on parse failure (will become sentinel via u16/u32 clamping).

def duration_sec:
    try (
        capture("(?<d>[0-9]+)T(?<h>[0-9]+):(?<m>[0-9]+):(?<s>[0-9]+)")
        | (
            (.d | tonumber) * 86400 +
            (.h | tonumber) * 3600  +
            (.m | tonumber) * 60    +
            (.s | tonumber)
        )
    )
    catch null;
