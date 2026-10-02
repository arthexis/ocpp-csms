from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

from ocpp_csms.time import utc_now_iso


@dataclass(frozen=True)
class Frame:
    at: str
    direction: str
    data: str


class FrameBuffer:
    def __init__(self, maxlen: int = 100) -> None:
        self.frames: deque[Frame] = deque(maxlen=maxlen)

    def remember(self, direction: str, data: Any) -> None:
        self.frames.append(Frame(utc_now_iso(), direction, str(data)))

    def dump(self, logger: logging.Logger) -> None:
        if not self.frames:
            logger.exception("frame")
            return
        lines = [f"{frame.at} {frame.direction} {frame.data}" for frame in self.frames]
        logger.exception("frame\n%s", "\n".join(lines))


class RecordedWebSocket:
    def __init__(self, websocket: Any, frames: FrameBuffer) -> None:
        self.websocket = websocket
        self.frames = frames

    async def recv(self) -> Any:
        frame = await self.websocket.recv()
        self.frames.remember("in", frame)
        return frame

    async def send(self, frame: Any) -> None:
        self.frames.remember("out", frame)
        await self.websocket.send(frame)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.websocket, name)
