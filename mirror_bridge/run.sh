#!/usr/bin/with-contenv bashio
# Read the add-on options and the Supervisor-provided MQTT credentials, then
# hand everything to the service as environment variables. Nothing is baked into
# the image and no secret is stored in the add-on's own options.
set -e

export MIRROR_LABEL="$(bashio::config 'label')"
export MIRROR_LABEL_RW="$(bashio::config 'label_rw')"
export MIRROR_BASE_TOPIC="$(bashio::config 'base_topic')"
export MIRROR_DISCOVERY_PREFIX="$(bashio::config 'discovery_prefix')"
export MIRROR_DEVICE_NAME="$(bashio::config 'device_name')"
export MIRROR_LOG_LEVEL="$(bashio::config 'log_level')"

# An explicitly configured account wins over the injected service credentials.
if [[ -n "$(bashio::config 'mqtt_user')" ]]; then
    export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export MQTT_USER="$(bashio::config 'mqtt_user')"
    export MQTT_PASSWORD="$(bashio::config 'mqtt_password')"
    bashio::log.info "Using the configured broker account ${MQTT_USER}"
elif bashio::services.available "mqtt"; then
    export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export MQTT_USER="$(bashio::services 'mqtt' 'username')"
    export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
else
    bashio::exit.nok "No MQTT service available - install and start the Mosquitto add-on."
fi

bashio::log.info "Starting Mirror Bridge (label=${MIRROR_LABEL}, base=${MIRROR_BASE_TOPIC})"
exec python3 -m mirror_bridge
