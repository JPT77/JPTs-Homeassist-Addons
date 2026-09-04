# LoRa Frame-Protokoll

Das Protokoll sitzt **oberhalb der SX1262-LoRa-PHY** (die Preamble, Sync-Word,
Header, Payload-Length und HW-CRC schon selbst liefert). Daher enthält der
Application-Header **keine** eigene Länge und **keine** eigene Prüfsumme.

## Frame-Aufbau

```
 +---------+---------+---------+---------+-------------------+
 | Byte 0  | Byte 1  | Byte 2  | Byte 3  | Byte 4 .. N       |
 +---------+---------+---------+---------+-------------------+
 | VVV FF  | TYPE    | SEQ     | TOPICID | PAYLOAD (0..247)  |
 +---------+---------+---------+---------+-------------------+
```

Maximaler LoRa-Payload beim SX1262 ist 255 Bytes → 251 Bytes App-Payload.

### Byte 0: Version + Flags

```
 bit  7 6 5 4 3 2 1 0
      V V V A R Y r r
      \___/ | | |
        |   | | +-- RETRY   : Frame ist eine Wiederholung
        |   | +---- ACK_RSP : dieser Frame IST das ACK
        |   +------ ACK_REQ : Empfänger MUSS ACK senden
        +---------- VERSION : aktuell 0b001 = v1
```

- `r` = reserviert (0)
- Andere Versionen dürfen zusätzliche Flags belegen; v1-Empfänger MÜSSEN
  Frames mit unbekannter Version verwerfen (aber im `debug`-Log melden).

### Byte 1: Type

| Wert   | Name    | Zweck                                                        |
| ------ | ------- | ------------------------------------------------------------ |
| `0x01` | MQTT    | Payload ist der UTF-8-Body einer MQTT-Nachricht              |
| `0x02` | CONTROL | Steuerkommando (Reboot, Config-Reload, Ping/Pong)            |
| `0x03` | ACK     | Bestätigung; Payload = 1 Byte Status (0=OK, 1=BUSY, 2=ERROR) |
| `0x04` | HELLO   | Boot-Announcement, Payload = JSON `{"role","fw","uptime"}`   |

### Byte 2: Sequence

Rollierende 8-Bit-Sequenznummer, pro Sender-Instanz separat gezählt.
Verwendung:

- ACK-Antworten spiegeln die Sequenznummer des Ursprungsframes.
- Empfänger kann Duplikate (RETRY-Wiederholungen bereits bestätigter Frames)
  am `(source_topic_id, seq)`-Paar erkennen und trotzdem ACK-en, aber nicht
  erneut in MQTT einspielen.

### Byte 3: Topic-ID

8-Bit-ID, deren Zuordnung zu MQTT-Topics in der Config steht:

```yaml
topics:
  - id: 1
    mqtt_topic: "solar/battery/soc"
    direction: rx           # rx=empfangen vom LoRa und in MQTT publishen
    qos: 1
    retained: true
  - id: 2
    mqtt_topic: "sensors/keller/temperature_bmp280"
    direction: tx           # tx=aus MQTT lesen und über LoRa senden
  - id: 3
    mqtt_topic: "home/keller/humidity"
    direction: bidir
```

Beide Seiten MÜSSEN dieselben ID→Topic-Mappings kennen. Es gibt bewusst
kein Discovery über den Air-Link (Bandbreite/Reliability). Wer sich
verkonfiguriert, sieht das an ignorierten Frames im `debug`-Log.

## ACK-Handling

1. Sender setzt `ACK_REQ`, merkt sich `(seq, timestamp, retries=0)` in einer
   Pending-Map.
2. Empfänger verarbeitet Frame, sendet Frame mit `TYPE=ACK`, `ACK_RSP=1`,
   selber `seq`, 1-Byte-Status als Payload.
3. Sender empfängt ACK → Eintrag löschen.
4. Ohne ACK nach `ack.timeout_ms` → `retries += 1`, `RETRY=1` setzen, erneut
   senden mit **selber** Sequenz. Backoff: `timeout_ms * backoff_factor^retries`.
5. Nach `ack.max_retries` → Frame verwerfen, im Log als `dropped` melden.
6. **CONTROL** und **HELLO** setzen ACK_REQ standardmäßig; **MQTT**-Frames
   nur wenn im Topic-Mapping `qos >= 1`. QoS 0 = fire-and-forget, kein ACK.

## Beispiel

MQTT-Publish `home/keller/humidity = 62.4` über LoRa, ID=3, seq=42, QoS 1:

```
Byte 0 = 0b001_10000  = 0x30    (V=1, ACK_REQ=1)
Byte 1 = 0x01                    (MQTT)
Byte 2 = 0x2A                    (seq 42)
Byte 3 = 0x03                    (topic id 3)
Payload = "62.4"  (4 Bytes UTF-8)
```

Antwort-ACK vom HA-Gateway:

```
Byte 0 = 0b001_01000  = 0x28    (V=1, ACK_RSP=1)
Byte 1 = 0x03                    (ACK)
Byte 2 = 0x2A                    (dieselbe seq)
Byte 3 = 0x03                    (dieselbe topic id — informativ)
Payload = 0x00                   (Status OK)
```

## Log-Level-Konventionen

| Level    | Was geloggt wird                                                     |
| -------- | -------------------------------------------------------------------- |
| `debug`  | alles inkl. RX-BAD (CRC-/Header-Fehler), IRQ-Register-Dumps, Retries |
| `info`   | jeder erfolgreich empfangene und gesendete Frame (1-Zeiler)          |
| `normal` | nur Fehler, ACK-Aufgaben, Start-/Stop-Meldungen                      |
