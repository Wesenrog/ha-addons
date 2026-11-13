"""The websocket client (0.11.0): a request never outlives its connection, and
a failing event handler takes nothing else down."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mirror_bridge.ha import HomeAssistant                        # noqa: E402


class FakeSocket:
    """Frames put on `frames` are received; None closes the socket."""

    def __init__(self):
        self.sent, self.frames = [], asyncio.Queue()

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        return self

    async def __anext__(self):
        frame = await self.frames.get()
        if frame is None:
            raise StopAsyncIteration
        return json.dumps(frame)


def connected():
    ha = HomeAssistant("token")
    ha._ws = FakeSocket()
    return ha, ha._ws


def test_a_reply_resolves_the_request_it_answers():
    async def scenario():
        ha, ws = connected()
        reader = asyncio.create_task(ha.run())
        call = asyncio.create_task(ha.get_states())
        await asyncio.sleep(0)
        ws.frames.put_nowait({"type": "result", "id": ws.sent[0]["id"],
                              "success": True, "result": ["a state"]})
        result = await asyncio.wait_for(call, 1)
        ws.frames.put_nowait(None)
        await reader
        return result
    assert asyncio.run(scenario()) == ["a state"]


def test_a_request_fails_when_the_socket_closes_under_it():
    async def scenario():
        ha, ws = connected()
        reader = asyncio.create_task(ha.run())
        call = asyncio.create_task(ha.get_states())
        await asyncio.sleep(0)
        ws.frames.put_nowait(None)
        await reader
        try:
            await asyncio.wait_for(call, 1)
        except ConnectionError:
            return "failed"
        return "answered"
    assert asyncio.run(scenario()) == "failed"


def test_a_failing_event_handler_does_not_stop_the_others():
    async def scenario():
        ha, ws = connected()
        heard = []

        async def broken(event):
            raise ValueError("no")

        async def working(event):
            heard.append(event["data"])

        ha._listeners = [("state_changed", broken), ("state_changed", working)]
        reader = asyncio.create_task(ha.run())
        for n in (1, 2):
            ws.frames.put_nowait({"type": "event", "event": {
                "event_type": "state_changed", "data": n}})
        ws.frames.put_nowait(None)
        await reader
        await asyncio.sleep(0.01)
        return heard
    assert asyncio.run(scenario()) == [1, 2]
