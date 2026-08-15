#!/usr/bin/with-contenv bashio
set -e

# Hent en valgfri opsjon, tom streng hvis den ikke er satt.
opt() {
    if bashio::config.has_value "$1"; then
        bashio::config "$1"
    else
        echo ""
    fi
}

if bashio::config.has_value 'mqtt_host'; then
    # Manuelt konfigurert broker har forrang.
    export MQTT_HOST="$(bashio::config 'mqtt_host')"
    export MQTT_PORT="$(bashio::config 'mqtt_port')"
    export MQTT_USER="$(opt 'mqtt_user')"
    export MQTT_PASS="$(opt 'mqtt_password')"
    bashio::log.info "Bruker MQTT-broker fra tilleggets konfigurasjon"
elif bashio::services.available "mqtt"; then
    # Faller tilbake pa brokeren Home Assistant allerede kjenner.
    export MQTT_HOST="$(bashio::services mqtt 'host')"
    export MQTT_PORT="$(bashio::services mqtt 'port')"
    export MQTT_USER="$(bashio::services mqtt 'username')"
    export MQTT_PASS="$(bashio::services mqtt 'password')"
    bashio::log.info "Bruker MQTT-tjenesten konfigurert i Home Assistant"
else
    bashio::log.error "Ingen MQTT-broker funnet."
    bashio::log.error "Sett mqtt_host (og evt. mqtt_user/mqtt_password) i"
    bashio::log.error "tilleggets konfigurasjon, eller sett opp MQTT-integrasjonen."
    exit 1
fi

export SRC_TOPIC="$(bashio::config 'src_topic')"
export SEQUENCE="$(bashio::config 'sequence')"
export STALE_AFTER="$(bashio::config 'stale_after')"
export F_U12="$(bashio::config 'field_u12')"
export F_U31="$(bashio::config 'field_u31')"
export F_U23="$(bashio::config 'field_u23')"
export F_I1="$(bashio::config 'field_i1')"
export F_I3="$(bashio::config 'field_i3')"
export F_P="$(bashio::config 'field_p')"
export F_Q="$(bashio::config 'field_q')"
export F_P_OUT="$(bashio::config 'field_p_out')"
export F_Q_OUT="$(bashio::config 'field_q_out')"

if [ -n "${MQTT_USER}" ]; then
    bashio::log.info "Kobler til ${MQTT_HOST}:${MQTT_PORT} som ${MQTT_USER}"
else
    bashio::log.info "Kobler til ${MQTT_HOST}:${MQTT_PORT} anonymt"
fi
bashio::log.info "Lytter pa ${SRC_TOPIC}"

exec python3 -u /app/il2_bridge.py
