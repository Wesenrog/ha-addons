ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip \
 && pip3 install --no-cache-dir --break-system-packages paho-mqtt

COPY it_nett_l2.py il2_bridge.py run.sh /app/
RUN chmod a+x /app/run.sh

WORKDIR /app
CMD ["/app/run.sh"]
