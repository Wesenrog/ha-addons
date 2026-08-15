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

# Aidon 3P3W via amsreader: UL1 = L1-L2, UL2 = L1-L3, UL3 = L2-L3.
# P og Q er import; PO og QO er eksport. Netto = import - eksport.
FIELDS = {
    "U12": os.environ.get("F_U12", "U1"),
    "U31": os.environ.get("F_U31", "U2"),
    "U23": os.environ.get("F_U23", "U3"),
    "I1": os.environ.get("F_I1", "I1"),
    "I3": os.environ.get("F_I3", "I3"),
    "P": os.environ.get("F_P", "P"),
    "Q": os.environ.get("F_Q", "Q"),
}

# Valgfrie eksportfelter. Mangler de, regnes eksporten som null - en
# melding blir ikke forkastet fordi anlegget ikke produserer.
FIELDS_OUT = {
    "PO": os.environ.get("F_P_OUT", "PO"),
    "QO": os.environ.get("F_Q_OUT", "QO"),
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

stats = {
    "received": 0,       # meldinger pa src_topic
    "incomplete": 0,     # manglet felter (typisk Aidon liste 1)
    "valid": 0,          # vellykkede beregninger
    "invalid": 0,        # konsistenssjekken slo ut
    "last_keys": None,   # noklene i siste ufullstendige melding
    "started": time.time(),
    "first_logged": False,
    "warned": False,
}


def dig(payload, key):
    """Hent en verdi, ogsaa fra en nostet 'data'-blokk."""
    if key in payload:
        return payload[key]
    data = payload.get("data")
    if isinstance(data, dict):
        return data.get(key)
    return None


# Feil som ikke loser seg av seg selv - nytter ikke aa prove igjen.
FATAL_CONNECT_CODES = {4, 5, 134, 135}


def on_connect(client, userdata, flags, reason_code, properties=None):
    code = getattr(reason_code, "value", reason_code)
    if code != 0:
        print(f"MQTT-tilkobling avvist: {reason_code}", file=sys.stderr)
        if code in FATAL_CONNECT_CODES:
            print(
                f"Brukernavn/passord avvist av brokeren paa "
                f"{MQTT_HOST}:{MQTT_PORT} (bruker: {MQTT_USER or '<anonym>'}).\n"
                f"Sett mqtt_user og mqtt_password i tilleggets konfigurasjon, "
                f"eller sjekk Mosquitto-loggen for aarsak.",
                file=sys.stderr,
            )
            client.disconnect()
            os._exit(1)
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

    stats["received"] += 1
    vals = {name: dig(payload, key) for name, key in FIELDS.items()}

    # Aidon liste 1 (hvert 2,5. sek) har bare aktiv effekt. Hopp over
    # ufullstendige meldinger, ellers regner vi paa blandede tidspunkter.
    if any(v is None for v in vals.values()):
        stats["incomplete"] += 1
        keys = sorted(payload.get("data", payload)) if isinstance(payload, dict) else []
        stats["last_keys"] = keys
        return

    # Netto effekt: import minus eksport.
    p_out = dig(payload, FIELDS_OUT["PO"]) or 0.0
    q_out = dig(payload, FIELDS_OUT["QO"]) or 0.0

    attrs = {"valid": False, "lead_deg": None, "alt_a": None, "error": None}
    try:
        sols = solve_missing_current(
            U12=float(vals["U12"]),
            U23=float(vals["U23"]),
            U31=float(vals["U31"]),
            I1=float(vals["I1"]),
            I3=float(vals["I3"]),
            P=float(vals["P"]) - float(p_out),
            Q=float(vals["Q"]) - float(q_out),
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
        stats["valid"] += 1
        if not stats["first_logged"]:
            stats["first_logged"] = True
            print(f"Forste vellykkede beregning: I_L2 = {best[0]:.2f} A "
                  f"(alternativ losning {alt[0]:.2f} A, lead {best[4]:.1f} grader)",
                  flush=True)
    except (ValueError, TypeError) as exc:
        stats["invalid"] += 1
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
    client.loop_start()

    while True:
        time.sleep(30)
        age = time.time() - stats["started"]

        if stats["received"] == 0:
            if age > 60 and not stats["warned"]:
                stats["warned"] = True
                print(f"ADVARSEL: ingen meldinger mottatt paa '{SRC_TOPIC}' "
                      f"etter {int(age)} s. Sjekk src_topic.", file=sys.stderr)
            continue

        if stats["valid"] == 0 and not stats["warned"]:
            stats["warned"] = True
            print(f"ADVARSEL: {stats['received']} meldinger mottatt, men ingen "
                  f"kunne brukes.", file=sys.stderr)
            if stats["last_keys"]:
                print(f"  felter i meldingen : {stats['last_keys']}", file=sys.stderr)
                print(f"  felter vi ser etter: {sorted(FIELDS.values())}",
                      file=sys.stderr)
                print("  Rett field_*-opsjonene i konfigurasjonen.", file=sys.stderr)
            continue

        if stats["valid"] and int(age) % 300 < 30:
            print(f"Status: {stats['received']} mottatt, "
                  f"{stats['valid']} beregnet, "
                  f"{stats['incomplete']} ufullstendige, "
                  f"{stats['invalid']} inkonsistente", flush=True)


if __name__ == "__main__":
    main()
