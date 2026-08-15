#!/usr/bin/env python3
"""
MQTT-bro som rekonstruerer strommen i L2 fra Aidon 3P3W HAN-data.

Publiserer hele maleoyeblikket - alle malte verdier pluss den avledede
I_L2 - som EN samlet JSON paa ett state-topic. Alle Home Assistant-
entitetene leser fra samme topic med hver sin value_template, slik at
de oppdateres atomisk fra samme sample.
"""

import json
import os
import sys
import time

import paho.mqtt.client as mqtt

from it_nett_l2 import solve_missing_current

UID = "it3_ams"
BASE = "ams/derived/it3"
STATE_TOPIC = f"{BASE}/state"
AVAIL_TOPIC = f"{BASE}/availability"

# Fra da broen bare publiserte I_L2 som en enkelt sensor. Tom melding
# med retain fjerner den utdaterte entiteten fra Home Assistant.
LEGACY_DISCOVERY = "homeassistant/sensor/it3_il2/config"

MQTT_HOST = os.environ.get("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

SRC_TOPIC = os.environ.get("SRC_TOPIC", "amsreader/power")
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

# Valgfrie eksportfelter. Mangler de, regnes eksporten som null.
FIELDS_OUT = {
    "PO": os.environ.get("F_P_OUT", "PO"),
    "QO": os.environ.get("F_Q_OUT", "QO"),
}

DEVICE = {
    "identifiers": [UID],
    "name": "Aidon IT-nett (beregnet)",
    "manufacturer": "Aidon",
    "model": "3P3W med rekonstruert L2",
}

# (object_id, navn, json-nokkel, device_class, enhet, diagnostikk)
SENSORS = [
    ("i_l1", "Strom L1", "i_l1", "current", "A", False),
    ("i_l2", "Strom L2", "i_l2", "current", "A", False),
    ("i_l3", "Strom L3", "i_l3", "current", "A", False),
    ("u_l1l2", "Spenning L1-L2", "u_l1l2", "voltage", "V", False),
    ("u_l1l3", "Spenning L1-L3", "u_l1l3", "voltage", "V", False),
    ("u_l2l3", "Spenning L2-L3", "u_l2l3", "voltage", "V", False),
    ("p", "Aktiv effekt", "p", "power", "W", False),
    ("q", "Reaktiv effekt", "q", "reactive_power", "var", False),
    ("i_l2_alt", "Strom L2 (forkastet losning)", "i_l2_alt", "current", "A", True),
    ("lead_deg", "Fasemargin", "lead_deg", None, "\u00b0", True),
]

last_good = {"value": None, "alt": None, "lead": None, "t": 0.0}

stats = {
    "received": 0,
    "incomplete": 0,
    "valid": 0,
    "invalid": 0,
    "last_keys": None,
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


def publish_discovery(client):
    """Alle entiteter peker paa samme state-topic - atomisk oppdatering."""
    for obj, name, key, dev_class, unit, diag in SENSORS:
        cfg = {
            "name": name,
            "unique_id": f"{UID}_{obj}",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.%s }}" % key,
            "availability_topic": AVAIL_TOPIC,
            "state_class": "measurement",
            "device": DEVICE,
        }
        if dev_class:
            cfg["device_class"] = dev_class
        if unit:
            cfg["unit_of_measurement"] = unit
        if diag:
            cfg["entity_category"] = "diagnostic"
        client.publish(f"homeassistant/sensor/{UID}/{obj}/config",
                       json.dumps(cfg), retain=True)

    client.publish(
        f"homeassistant/binary_sensor/{UID}/valid/config",
        json.dumps({
            "name": "Beregning gyldig",
            "unique_id": f"{UID}_valid",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.valid else 'OFF' }}",
            "availability_topic": AVAIL_TOPIC,
            "device_class": "problem",
            "payload_on": "OFF",
            "payload_off": "ON",
            "entity_category": "diagnostic",
            "device": DEVICE,
        }),
        retain=True,
    )

    # Fjern sensoren fra da broen bare publiserte I_L2 alene.
    client.publish(LEGACY_DISCOVERY, "", retain=True)


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
    publish_discovery(client)
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
        stats["last_keys"] = sorted(payload.get("data", payload))
        return

    # Netto effekt: import minus eksport.
    p_net = float(vals["P"]) - float(dig(payload, FIELDS_OUT["PO"]) or 0.0)
    q_net = float(vals["Q"]) - float(dig(payload, FIELDS_OUT["QO"]) or 0.0)

    state = {
        "i_l1": round(float(vals["I1"]), 2),
        "i_l3": round(float(vals["I3"]), 2),
        "u_l1l2": round(float(vals["U12"]), 1),
        "u_l1l3": round(float(vals["U31"]), 1),
        "u_l2l3": round(float(vals["U23"]), 1),
        "p": round(p_net, 1),
        "q": round(q_net, 1),
        "ts": int(time.time()),
        "valid": False,
        "error": None,
    }

    try:
        sols = solve_missing_current(
            U12=float(vals["U12"]), U23=float(vals["U23"]),
            U31=float(vals["U31"]), I1=float(vals["I1"]),
            I3=float(vals["I3"]), P=p_net, Q=q_net, sequence=SEQUENCE,
        )
        best, alt = sols[0], sols[1]
        last_good.update(value=best[0], alt=alt[0], lead=best[4], t=time.time())
        state["valid"] = True
        stats["valid"] += 1
        if not stats["first_logged"]:
            stats["first_logged"] = True
            print(f"Forste vellykkede beregning: I_L2 = {best[0]:.2f} A "
                  f"(alternativ losning {alt[0]:.2f} A, lead {best[4]:.1f} grader)",
                  flush=True)
    except (ValueError, TypeError) as exc:
        stats["invalid"] += 1
        state["error"] = str(exc)

    # Malte verdier er alltid ferske. Bare den avledede I_L2 holdes fra
    # forrige gyldige beregning naar konsistenssjekken slaar ut.
    age = time.time() - last_good["t"]
    state["age_s"] = round(age, 1)
    if last_good["value"] is None:
        state["i_l2"] = None
        state["i_l2_alt"] = None
        state["lead_deg"] = None
    else:
        state["i_l2"] = round(last_good["value"], 2)
        state["i_l2_alt"] = round(last_good["alt"], 2)
        state["lead_deg"] = round(last_good["lead"], 1)

    if last_good["value"] is None or age > STALE_AFTER:
        client.publish(AVAIL_TOPIC, "offline", retain=True)
    else:
        client.publish(AVAIL_TOPIC, "online", retain=True)

    # Ett topic, en melding - alle entitetene oppdateres samtidig.
    client.publish(STATE_TOPIC, json.dumps(state))


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
