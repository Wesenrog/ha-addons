"""Home Assistant websocket client, authenticated with the Supervisor token.

Only the handful of commands this add-on needs, kept deliberately small: the
entity and label registries to work out membership, a state subscription to know
what to publish, and service calls for the command path.
"""
import asyncio, json, logging

import websockets

_LOG = logging.getLogger(__name__)
URL = "ws://supervisor/core/websocket"


class HomeAssistant:
    def __init__(self, token):
        self._token = token
        self.listeners_to_restore = []
        self._ws = None
        self._id = 0
        self._pending = {}
        self._listeners = []

    async def connect(self):
        # A fresh connection means fresh subscription ids, so drop any state
        # from the previous session before authenticating again.
        self._id = 0
        self._pending = {}
        self._listeners = []
        self._ws = await websockets.connect(URL, max_size=16 * 1024 * 1024)
        greeting = json.loads(await self._ws.recv())
        if greeting.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting: {greeting}")
        await self._ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        reply = json.loads(await self._ws.recv())
        if reply.get("type") != "auth_ok":
            raise RuntimeError(f"auth failed: {reply}")
        _LOG.info("connected to Home Assistant %s", reply.get("ha_version"))

    async def _send(self, payload):
        self._id += 1
        payload["id"] = self._id
        future = asyncio.get_running_loop().create_future()
        self._pending[self._id] = future
        await self._ws.send(json.dumps(payload))
        return await future

    # --- the commands this add-on uses --------------------------------------
    async def entity_registry(self):
        return await self._send({"type": "config/entity_registry/list"})

    async def label_registry(self):
        return await self._send({"type": "config/label_registry/list"})

    async def device_registry(self):
        return await self._send({"type": "config/device_registry/list"})

    async def get_states(self):
        return await self._send({"type": "get_states"})

    async def call_service(self, domain, service, target, data):
        return await self._send({
            "type": "call_service", "domain": domain, "service": service,
            "target": target, "service_data": data})

    async def subscribe(self, event_type, callback):
        """Register a coroutine for an event type. Filtering happens here in
        Python rather than in a Jinja condition, which is why this scales to
        every state change on a busy instance."""
        self._listeners.append((event_type, callback))
        if (event_type, callback) not in self.listeners_to_restore:
            self.listeners_to_restore.append((event_type, callback))
        await self._send({"type": "subscribe_events", "event_type": event_type})

    async def run(self):
        """Read messages until the socket closes, then return.

        Returning rather than raising lets the caller re-establish everything -
        a Home Assistant restart drops this socket, and the add-on has to come
        back on its own rather than relying on the Supervisor restarting it.
        """
        async for raw in self._ws:
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "result":
                future = self._pending.pop(msg["id"], None)
                if future and not future.done():
                    if msg.get("success"):
                        future.set_result(msg.get("result"))
                    else:
                        future.set_exception(RuntimeError(str(msg.get("error"))))
            elif kind == "event":
                event = msg["event"]
                for event_type, callback in self._listeners:
                    if event["event_type"] == event_type:
                        asyncio.create_task(callback(event))
