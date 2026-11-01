#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

if bashio::config.has_value 'mqtt_host'; then
    export MQTT_HOST="$(bashio::config 'mqtt_host')"
    export MQTT_PORT="$(bashio::config 'mqtt_port' 1883)"
    if bashio::config.has_value 'mqtt_username'; then
        export MQTT_USERNAME="$(bashio::config 'mqtt_username')"
        export MQTT_PASSWORD="$(bashio::config 'mqtt_password')"
    fi
    bashio::log.info "Using MQTT broker ${MQTT_HOST}:${MQTT_PORT} from the add-on options"
elif bashio::services.available 'mqtt'; then
    export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
    export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
    bashio::log.info "Using the Mosquitto broker add-on at ${MQTT_HOST}:${MQTT_PORT}"
else
    bashio::exit.nok "No MQTT broker found. Install the Mosquitto broker add-on or set mqtt_host"
fi

cd /app
exec /opt/venv/bin/python3 -m homely_bridge
