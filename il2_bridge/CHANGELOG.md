# Endringslogg

## 1.0.2

- Mulig a sette mqtt_host, mqtt_port, mqtt_user og mqtt_password manuelt.
  Passordfeltet maskeres i UI-et.
- Uten mqtt_host brukes fortsatt MQTT-tjenesten fra Home Assistant.
- services endret fra mqtt:need til mqtt:want, slik at tillegget ogsa
  starter mot en broker utenfor Home Assistant.

## 1.0.1

- Fikset byggefeil: oppgir base-image eksplisitt. Supervisor 2026.04.0
  sender ikke lenger BUILD_FROM automatisk til app-er uten build.yaml.
- Lagt til paakrevde io.hass.*-labels.

## 1.0.0

- Forste versjon. Rekonstruerer I_L2 fra I_L1, I_L3, U12/U23/U31, P og Q.
- Publiserer via MQTT Discovery med valid, lead_deg og alt_a som attributter.
