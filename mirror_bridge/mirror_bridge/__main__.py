"""Entry point: wire the three pieces together and stay running."""
import asyncio, logging

from .broker import Broker
from .bridge import Bridge
from .config import Config
from .ha import HomeAssistant


async def main():
    cfg = Config()
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    log = logging.getLogger("mirror_bridge")

    loop = asyncio.get_running_loop()
    holder = {}

    def on_command(topic, payload):
        # paho calls this from its own thread, so hand back to the loop.
        bridge = holder.get("bridge")
        if bridge:
            asyncio.run_coroutine_threadsafe(bridge.handle_command(topic, payload), loop)

    def on_reconnect():
        bridge = holder.get("bridge")
        if bridge:
            asyncio.run_coroutine_threadsafe(bridge.reannounce(), loop)

    broker = Broker(cfg, on_command, on_reconnect)
    broker.start()

    backoff = 1
    while True:
        try:
            ha = HomeAssistant(cfg.supervisor_token)
            await ha.connect()
            bridge = Bridge(cfg, ha, broker)
            holder["bridge"] = bridge
            reader = asyncio.create_task(ha.run())
            await bridge.refresh_members()
            await ha.subscribe("state_changed", bridge.on_state_changed)
            await ha.subscribe("entity_registry_updated", bridge.on_registry_updated)
            log.info("mirror-bridge running")
            backoff = 1
            await reader
            log.warning("Home Assistant closed the connection - reconnecting")
        except Exception as exc:               # noqa: BLE001 - stay alive
            log.warning("Home Assistant link failed (%s) - retrying in %ds", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


asyncio.run(main())
