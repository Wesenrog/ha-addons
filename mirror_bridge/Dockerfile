# Supervisor 2026.04.0+ sender ikke lenger BUILD_FROM automatisk, saa
# defaultverdien gjor at bygget virker bade med og uten build.yaml.
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:latest
FROM ${BUILD_FROM}

# paho-mqtt for the broker, websockets for Home Assistant's API. Both are pinned
# so a rebuild months from now produces the same image.
# websockets carries a C extension and there may be no musl/aarch64 wheel, so
# build tooling goes in as a virtual package and comes straight back out rather
# than bloating the image.
RUN apk add --no-cache --virtual .build-deps gcc musl-dev python3-dev \
    && pip3 install --no-cache-dir \
        "paho-mqtt==2.1.0" \
        "websockets==13.1" \
    && apk del .build-deps

COPY mirror_bridge /opt/mirror_bridge
# run.sh execs from /, so the package needs to be importable by path rather than
# by working directory.
ENV PYTHONPATH=/opt
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
