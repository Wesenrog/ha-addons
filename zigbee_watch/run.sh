#!/usr/bin/with-contenv bashio
# Read the add-on options and the Supervisor-provided MQTT credentials, then
# hand everything to the service as environment variables.
set -e

for opt in z2m_base_topic scan_time scan_timeout_min retention_days lqi_drop \
           error_min_count error_factor error_baseline_days \
           timeout_mains_min timeout_battery_min log_level; do
    export "ZW_${opt^^}=$(bashio::config "${opt}")"
done

if bashio::services.available "mqtt"; then
    export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export MQTT_USER="$(bashio::services 'mqtt' 'username')"
    export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
else
    bashio::exit.nok "No MQTT service available - install and start the Mosquitto add-on."
fi

mkdir -p /share/zigbee_watch
bashio::log.info "Starting Zigbee Watch (Zigbee2MQTT base topic ${ZW_Z2M_BASE_TOPIC}, scan at ${ZW_SCAN_TIME})"
exec python3 -m zigbee_watch
