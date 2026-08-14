#!/usr/bin/env python3
"""
MQTT-bro som rekonstruerer strommen i L2 fra Aidon 3P3W HAN-data
og publiserer den som en Home Assistant-sensor via MQTT Discovery.

Konfigurasjon leses fra miljovariabler satt av run.sh (bashio).
"""

import json
import os
import sys
import time

import paho.mqtt.client as mqtt

from it_nett_l2 import solve_missing_current

UID = "it3_il2"
DISC_TOPIC = f"homeassistant/sensor/{UID}/config"
STATE_TOPIC = f"ams/derived/{UID}/state"
ATTR_TOPIC = f"ams/derived/{UID}/attributes"
AVAIL_TOPIC = f"ams/derived/{UID}/availability"

MQTT_HOST = os.environ.get("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

SRC_TOPIC = os.environ.get("SRC_TOPIC", "ams/meter")
SEQUENCE = int(os.environ.get("SEQUENCE", "-1"))
STALE_AFTER = float(os.environ.get("STALE_AFTER", "30"))

# Aidon 3P3W: UL1 = L1-L2, UL2 = L1-L3, UL3 = L2-L3
FIELDS = {
    "U12": os.environ.get("F_U12", "U1"),
    "U31": os.environ.get("F_U31", "U2"),
    "U23": os.environ.get("F_U23", "U3"),
    "I1": os.environ.get("F_I1", "I1"),
    "I3": os.environ.get("F_I3", "I3"),
    "P": os.environ.get("F_P", "P"),
    "Q": os.environ.get("F_Q", "Q"),
}

DISCOVERY = {
    "name": "Strom L2 (beregnet)",
    "unique_id": UID,
    "state_topic": STATE_TOPIC,
    "json_attributes_topic": ATTR_TOPIC,
    "availability_topic": AVAIL_TOPIC,
    "device_class": "current",
    "unit_of_measurement": "A",
    "state_class": "measurement",
    "suggested_display_precision": 1,
    "device": {
        "identifiers": ["aidon_han"],
        "name": "Aidon stromaaler",
        "manufacturer": "Aidon",
        "model": "3P3W (IT-nett)",
    },
}

last_good = {"value": None, "t": 0.0}


def dig(payload, key):
    """Hent en verdi, ogsaa fra en nostet 'data'-blokk."""
    if key in payload:
        return payload[key]
    data = payload.get("data")
    if isinstance(data, dict):
        return data.get(key)
    return None


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        print(f"MQTT-tilkobling avvist: {reason_code}", file=sys.stderr)
        return
    print(f"Tilkoblet. Abonnerer paa {SRC_TOPIC}", flush=True)
    client.publish(DISC_TOPIC, json.dumps(DISCOVERY), retain=True)
    client.subscribe(SRC_TOPIC)


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload)
    except (ValueError, UnicodeDecodeError):
        return
    if not isinstance(payload, dict):
        return

    vals = {name: dig(payload, key) for name, key in FIELDS.items()}

    # Aidon liste 1 (hvert 2,5. sek) har bare aktiv effekt. Hopp over
    # ufullstendige meldinger, ellers regner vi paa blandede tidspunkter.
    if any(v is None for v in vals.values()):
        return

    attrs = {"valid": False, "lead_deg": None, "alt_a": None, "error": None}
    try:
        sols = solve_missing_current(
            U12=float(vals["U12"]),
            U23=float(vals["U23"]),
            U31=float(vals["U31"]),
            I1=float(vals["I1"]),
            I3=float(vals["I3"]),
            P=float(vals["P"]),
            Q=float(vals["Q"]),
            sequence=SEQUENCE,
        )
        best, alt = sols[0], sols[1]
        last_good["value"] = best[0]
        last_good["t"] = time.time()
        attrs.update(
            valid=True,
            lead_deg=round(best[4], 1),
            alt_a=round(alt[0], 2),
        )
    except (ValueError, TypeError) as exc:
        attrs["error"] = str(exc)

    age = time.time() - last_good["t"]
    attrs["age_s"] = round(age, 1)

    if last_good["value"] is None or age > STALE_AFTER:
        client.publish(AVAIL_TOPIC, "offline", retain=True)
    else:
        client.publish(AVAIL_TOPIC, "online", retain=True)
        client.publish(STATE_TOPIC, f"{last_good['value']:.2f}")
    client.publish(ATTR_TOPIC, json.dumps(attrs))


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message
    client.will_set(AVAIL_TOPIC, "offline", retain=True)
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_forever()


if __name__ == "__main__":
    main()
