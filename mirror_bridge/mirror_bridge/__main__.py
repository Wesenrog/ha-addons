"""Entry point: wire the three pieces together and stay running."""
import asyncio, logging, signal

from .broker import Broker
from .bridge import Bridge
from .config import Config
from .ha import HomeAssistant


async def serve(cfg, broker, holder, log):
    """Hold a Home Assistant connection, and get a new one when it goes."""
    backoff = 1
    while True:
        reader = None
        failed = False
        try:
            ha = HomeAssistant(cfg.supervisor_token)
            await ha.connect()
            bridge = Bridge(cfg, ha, broker)
            holder["bridge"] = bridge
            reader = asyncio.create_task(ha.run())
            # A new Bridge knows nothing of what an earlier one published, so
            # what the broker still holds is read back and checked against the
            # membership: a label taken off while this was down is retracted.
            retained = await asyncio.to_thread(
                broker.retained,
                (f"{cfg.discovery_prefix}/#", f"{cfg.base_topic}/state/#"))
            await bridge.refresh_members()
            bridge.reconcile(retained)
            await ha.subscribe("state_changed", bridge.on_state_changed)
            await ha.subscribe("entity_registry_updated", bridge.on_registry_updated)
            broker.set_alive(True)
            log.info("mirror-bridge running")
            backoff = 1
            await reader
            log.warning("Home Assistant closed the connection - reconnecting")
        except Exception as exc:               # noqa: BLE001 - stay alive
            log.warning("Home Assistant link failed (%s) - retrying in %ds", exc, backoff)
            failed = True
        finally:
            # Without Home Assistant nothing here is fresh, so say so at once.
            broker.set_alive(False)
            if reader:
                if reader.done() and not reader.cancelled():
                    reader.exception()      # fetched, so it is not reported twice
                reader.cancel()
        if failed:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def main():
    cfg = Config()
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    log = logging.getLogger("mirror_bridge")

    loop = asyncio.get_running_loop()
    holder = {}

    def log_failure(future):
        # Nobody awaits these, so an exception has to be fetched to be seen.
        if not future.cancelled() and future.exception():
            log.error("command handling failed", exc_info=future.exception())

    def on_command(topic, payload):
        # paho calls this from its own thread, so hand back to the loop.
        bridge = holder.get("bridge")
        if bridge:
            asyncio.run_coroutine_threadsafe(
                bridge.handle_command(topic, payload), loop).add_done_callback(log_failure)

    def on_reconnect():
        bridge = holder.get("bridge")
        if bridge:
            asyncio.run_coroutine_threadsafe(
                bridge.reannounce(), loop).add_done_callback(log_failure)

    broker = Broker(cfg, on_command, on_reconnect)
    broker.start()

    stopping = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopping.set)
    server = asyncio.create_task(serve(cfg, broker, holder, log))
    stop = asyncio.create_task(stopping.wait())
    await asyncio.wait({server, stop}, return_when=asyncio.FIRST_COMPLETED)
    log.info("stopping")
    stop.cancel()
    server.cancel()
    failure = (await asyncio.gather(server, return_exceptions=True))[0]
    broker.stop()
    if isinstance(failure, Exception):
        # serve() is not meant to return; leave so the watchdog starts it again.
        raise failure


asyncio.run(main())
