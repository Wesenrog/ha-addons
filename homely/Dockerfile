# BUILD_FROM is set per architecture from build.yaml
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install only pure-Python wheels: the exact pins in requirements.txt are
# downloaded as platform-independent wheels, so nothing is ever compiled
# and every architecture runs the same code.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip download --no-cache-dir --no-deps --only-binary=:all: \
        --platform any --implementation py -r /tmp/requirements.txt -d /tmp/wheels \
    && /opt/venv/bin/pip install --no-cache-dir --no-index --no-deps /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels /tmp/requirements.txt

COPY homely_bridge /app/homely_bridge
COPY run.sh /run.sh
RUN chmod a+x /run.sh

WORKDIR /app
CMD ["/run.sh"]
