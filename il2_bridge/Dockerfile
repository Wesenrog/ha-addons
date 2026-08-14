# Supervisor 2026.04.0+ sender ikke lenger BUILD_FROM automatisk.
# Defaultverdien gjor at bygget virker bade med og uten build.yaml.
ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM ${BUILD_FROM}

ARG BUILD_VERSION=1.0.1
ARG BUILD_ARCH=aarch64

RUN apk add --no-cache python3 py3-pip \
 && pip3 install --no-cache-dir --break-system-packages paho-mqtt

COPY it_nett_l2.py il2_bridge.py run.sh /app/
RUN chmod a+x /app/run.sh

WORKDIR /app
CMD ["/app/run.sh"]

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"
