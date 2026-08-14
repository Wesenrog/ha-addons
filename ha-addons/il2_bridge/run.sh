#!/usr/bin/with-contenv bashio
set -e

if ! bashio::services.available "mqtt"; then
    bashio::log.error "Ingen MQTT-tjeneste konfigurert i Home Assistant"
    exit 1
fi

export MQTT_HOST="$(bashio::services mqtt 'host')"
export MQTT_PORT="$(bashio::services mqtt 'port')"
export MQTT_USER="$(bashio::services mqtt 'username')"
export MQTT_PASS="$(bashio::services mqtt 'password')"

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

bashio::log.info "Kobler til ${MQTT_HOST}:${MQTT_PORT}, lytter paa ${SRC_TOPIC}"
exec python3 -u /app/il2_bridge.py
