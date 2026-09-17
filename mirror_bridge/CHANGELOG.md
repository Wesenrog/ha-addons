# Changelog

## 0.1.0 - unreleased

Skeleton. Add-on packaging, the domain-to-mirror class table, and the runtime
structure: label-driven membership, discovery publishing, retraction on removal,
and a command path validated against the entity's own attributes. Built and running on the Pi against a `test_mirror` prefix; the read path is
verified end to end and the command path is not yet exercised.

Fixed during first run: `on_connect` logged success without checking paho's
reason code, which hid an authentication-rejection reconnect loop behind five
cheerful "connected" lines. Added explicit `mqtt_user`/`mqtt_password` options
because `services: ["mqtt:need"]` is unusable while the Mosquitto add-on defines
a manual login colliding with the Supervisor's service account name.
