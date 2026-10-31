"""严格 UTF-8 与 SSE 分帧；网络分片不能改变模型文本。"""
from __future__ import annotations

import codecs
import re


class SseDataDecoder:
    def __init__(self, max_frame_bytes: int = 8 * 1024 * 1024):
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self.buffer = ""
        self.data: list[str] = []
        self.size = 0
        self.maximum = max_frame_bytes

    def feed(self, value: bytes, *, final: bool = False) -> list[str]:
        try:
            self.buffer += self.decoder.decode(value, final=final)
        except UnicodeDecodeError as exc:
            raise RuntimeError("模型流包含无效 UTF-8，拒绝用替代字符改写原文") from exc
        frames = []
        while match := re.search(r"\r\n|\r|\n", self.buffer):
            if not final and match.group() == "\r" and match.end() == len(self.buffer):
                break
            line, self.buffer = self.buffer[:match.start()], self.buffer[match.end():]
            self._line(line, frames)
        if len(self.buffer.encode("utf-8")) + self.size > self.maximum:
            raise RuntimeError("模型 SSE 数据帧过大")
        if final:
            if self.buffer:
                self._line(self.buffer, frames)
                self.buffer = ""
            self._line("", frames)
        return frames

    def _line(self, line: str, frames: list[str]) -> None:
        if line == "":
            if self.data:
                data = "\n".join(self.data)
                if data:
                    frames.append(data)
            self.data, self.size = [], 0
        elif line == "data" or line.startswith("data:"):
            value = line[5:] if line.startswith("data:") else ""
            if value.startswith(" "):
                value = value[1:]
            self.data.append(value)
            self.size += len(value.encode("utf-8")) + 1
            if self.size > self.maximum:
                raise RuntimeError("模型 SSE 数据帧过大")
