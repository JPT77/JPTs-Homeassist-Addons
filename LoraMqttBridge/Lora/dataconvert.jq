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
# Parses Tasmota Wifi.Downtime / Uptime format: "0T00:01:09" or "00:01:09" -> total seconds (integer)
# Also accepts numeric strings or raw numbers. Returns null on parse failure.

def duration_sec:
    if . == null then null
    elif type == "number" then .
    elif type == "string" then
        try (
            capture("((?<d>[0-9]+)T)?(?<h>[0-9]+):(?<m>[0-9]+):(?<s>[0-9]+)")
            | (
                ((.d // "0") | tonumber) * 86400 +
                (.h | tonumber) * 3600  +
                (.m | tonumber) * 60    +
                (.s | tonumber)
            )
        )
        catch (try tonumber catch null)
    else null end;

# --- LoRa Epoch & Timestamp Helpers (Base epoch: 2020-01-01T00:00:00Z = 1577836800) ---

def lora_epoch: 1577836800;

# Converts ISO8601 string, unix timestamp number, or null into LoRa uint32 seconds since 2020-01-01.
# Returns 4294967295 (0xFFFFFFFF) if input is null or conversion fails.
def to_lora_time:
    if . == null then 4294967295
    elif type == "number" then
        if . >= 1577836800 then (. - 1577836800 | u32)
        else u32 end
    elif type == "string" then
        try (
            (if endswith("Z") or contains("+") or (split("T")[1] // "" | contains("-")) then . else . + "Z" end)
            | fromdateiso8601
            | (. - 1577836800)
            | u32
        ) catch 4294967295
    else 4294967295 end;

# Converts LoRa uint32 seconds since 2020-01-01 back to ISO8601 string (or null if invalid / sentinel).
def from_lora_time:
    if . == null or . == 4294967295 then null
    else
        try (
            (. + 1577836800) | todateiso8601
        ) catch null
    end;
