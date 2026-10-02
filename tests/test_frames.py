import logging

import pytest

from ocpp_csms.frames import FrameBuffer, RecordedWebSocket


class FakeWebSocket:
    def __init__(self) -> None:
        self.incoming = '[2,"abc","Heartbeat",{}]'
        self.sent = []
        self.subprotocol = "ocpp1.6"

    async def recv(self):
        return self.incoming

    async def send(self, frame):
        self.sent.append(frame)


@pytest.mark.asyncio
async def test_recorded_websocket_keeps_exact_recent_frames():
    raw = FakeWebSocket()
    frames = FrameBuffer(maxlen=2)
    websocket = RecordedWebSocket(raw, frames)

    assert await websocket.recv() == raw.incoming
    await websocket.send('[3,"abc",{}]')
    frames.remember("in", "third")

    assert [(frame.direction, frame.data) for frame in frames.frames] == [
        ("out", '[3,"abc",{}]'),
        ("in", "third"),
    ]
    assert websocket.subprotocol == "ocpp1.6"


def test_dump_is_one_terse_exception_record(caplog):
    frames = FrameBuffer(maxlen=2)
    frames.remember("in", "first")
    frames.remember("out", "second")
    logger = logging.getLogger("test.frames")

    try:
        raise OSError("database unavailable")
    except OSError:
        with caplog.at_level(logging.ERROR, logger="test.frames"):
            frames.dump(logger)

    assert len(caplog.records) == 1
    assert caplog.records[0].message.startswith("frame\n")
    assert " in first" in caplog.records[0].message
    assert " out second" in caplog.records[0].message
    assert caplog.records[0].exc_info is not None
