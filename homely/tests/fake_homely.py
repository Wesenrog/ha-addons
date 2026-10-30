"""A fake Homely SDK server (REST + Socket.IO) for tests."""

import asyncio
import copy
import socket
import threading
import time
import uuid

import socketio
from aiohttp import web


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeHomely:
    def __init__(self, home: dict, username="user@example.com", password="secret", expires_in=60):
        self.home = copy.deepcopy(home)
        self.username = username
        self.password = password
        self.expires_in = expires_in
        self.valid_tokens: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.calls: list[str] = []
        self.home_times: list[float] = []  # monotonic time of each /home request
        self.home_errors: list[tuple[int, dict]] = []  # queued (status, headers) for /home
        self.ws_sids: list[str] = []
        self.ws_rejected = 0
        self.ws_connects = 0
        self.reject_ws = False
        self.home_delay = 0.0
        self.port = free_port()
        self.loop = asyncio.new_event_loop()
        # Short ping settings so dead connections are found quickly in tests
        self.sio = socketio.AsyncServer(async_mode="aiohttp", ping_interval=1, ping_timeout=1)
        self._runner = None

    # -- helpers --
    def _issue(self):
        access, refresh = uuid.uuid4().hex, uuid.uuid4().hex
        self.valid_tokens.add(access)
        self.refresh_tokens.add(refresh)
        return web.json_response({
            "access_token": access, "expires_in": self.expires_in,
            "refresh_token": refresh, "refresh_expires_in": 1800, "token_type": "bearer",
        })

    def _authorized(self, request) -> bool:
        header = request.headers.get("Authorization", "")
        return header.startswith("Bearer ") and header[7:] in self.valid_tokens

    # -- handlers --
    async def token(self, request):
        self.calls.append("login")
        body = await request.json()
        if body.get("username") != self.username or body.get("password") != self.password:
            return web.json_response({"statusCode": 401, "message": "Unauthorized"}, status=401)
        return self._issue()

    async def refresh(self, request):
        self.calls.append("refresh")
        body = await request.json()
        if body.get("refresh_token") not in self.refresh_tokens:
            return web.json_response({"statusCode": 400, "message": "Refresh token expired"}, status=400)
        self.refresh_tokens.discard(body["refresh_token"])
        return self._issue()

    async def locations(self, request):
        self.calls.append("locations")
        if not self._authorized(request):
            return web.json_response({"statusCode": 401, "message": "Unauthorized"}, status=401)
        return web.json_response([{
            "name": self.home["name"], "role": "ADMIN", "userId": "u1",
            "locationId": self.home["locationId"], "gatewayserial": self.home["gatewayserial"],
        }])

    async def home_handler(self, request):
        self.calls.append("home")
        self.home_times.append(time.monotonic())
        if not self._authorized(request):
            return web.json_response({"statusCode": 401, "message": "Unauthorized"}, status=401)
        if self.home_delay:
            await asyncio.sleep(self.home_delay)
        if self.home_errors:
            status, headers = self.home_errors.pop(0)
            return web.json_response({"statusCode": status, "message": "Too many"}, status=status, headers=headers)
        return web.json_response(self.home)

    async def _start(self):
        app = web.Application()
        app.router.add_post("/homely/oauth/token", self.token)
        app.router.add_post("/homely/oauth/refresh-token", self.refresh)
        app.router.add_get("/homely/locations", self.locations)
        app.router.add_get("/homely/home/{location_id}", self.home_handler)
        self.sio.attach(app)

        @self.sio.event
        async def connect(sid, environ, auth=None):
            query = environ.get("QUERY_STRING", "")
            from urllib.parse import parse_qs
            params = parse_qs(query)
            token = (params.get("token") or [""])[0]
            if self.reject_ws or not token.startswith("Bearer ") or token[7:] not in self.valid_tokens \
                    or (params.get("locationId") or [""])[0] != self.home["locationId"]:
                self.ws_rejected += 1
                return False
            self.ws_sids.append(sid)
            self.ws_connects += 1

        @self.sio.event
        async def disconnect(sid, *args):
            if sid in self.ws_sids:
                self.ws_sids.remove(sid)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()

    def start(self):
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._start())
            ready.set()
            self.loop.run_forever()

        threading.Thread(target=run, daemon=True).start()
        assert ready.wait(10)

    def stop(self):
        async def shutdown():
            await self._runner.cleanup()
        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)

    def emit(self, data: dict):
        asyncio.run_coroutine_threadsafe(self.sio.emit("event", data), self.loop).result(5)

    def kick_websockets(self):
        async def kick():
            for sid in list(self.ws_sids):
                await self.sio.disconnect(sid)
        asyncio.run_coroutine_threadsafe(kick(), self.loop).result(5)

    @property
    def api_url(self):
        return f"http://127.0.0.1:{self.port}/homely/"

    @property
    def ws_url(self):
        return f"http://127.0.0.1:{self.port}"


class FreezableProxy:
    """TCP proxy that can stop forwarding without closing anything.

    freeze() makes every open connection go silent in both directions, like a
    network that drops packets with no FIN or RST. New connections still work.
    """

    def __init__(self, target_port: int):
        self.target_port = target_port
        self.port = free_port()
        self._frozen: list[threading.Event] = []
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", self.port))
        self._server.listen()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            upstream = socket.create_connection(("127.0.0.1", self.target_port))
            frozen = threading.Event()
            self._frozen.append(frozen)
            for src, dst in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pump, args=(src, dst, frozen), daemon=True).start()

    @staticmethod
    def _pump(src, dst, frozen):
        try:
            while not frozen.is_set():
                data = src.recv(65536)
                if frozen.is_set():
                    return
                if not data:
                    dst.shutdown(socket.SHUT_WR)
                    return
                dst.sendall(data)
        except OSError:
            return

    def freeze(self):
        for event in self._frozen:
            event.set()
        self._frozen = []

    def close(self):
        self._server.close()
