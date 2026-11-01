# Supervisor 2026.04.0+ no longer passes BUILD_FROM automatically, so the
# default keeps the build working with or without build.yaml.
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.24-2026.10.3
FROM ${BUILD_FROM}

# Pure-Python dependencies only, pinned in requirements.txt: no compiler needed.
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY zigbee_watch /opt/zigbee_watch
# run.sh execs from /, so the package must be importable by path.
ENV PYTHONPATH=/opt
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
