# Copyright (c) 2025-2026 iiPython

# Modules
import re
import typing
import asyncio
from dataclasses import dataclass

import struct
from base64 import b64encode
from hashlib import sha1

__version__ = "shdw/1.2.0"

# Initialization
UNRESERVED  = b"A-Za-z0-9\\-._~"
SUB_DELIMS  = b"!$&'()*+,;="
PCHAR       = UNRESERVED + SUB_DELIMS + b":@%"
QUERY_CHARS = PCHAR + b"/?"

REQUEST_TARGET = b"/[" + PCHAR + b"/]*(?:\\?[" + QUERY_CHARS + b"]*)?"
TOKEN       = b"[!#$%&'*+\\-\\.^_`|~0-9A-Za-z]+"
VERSION     = b"(\\d+(?:\\.\\d+)?)"
FIELD_VALUE = b"[\\x20-\\x7E]*"

HTTP_REQUEST_LINE = re.compile(b"^(" + TOKEN + b") (" + REQUEST_TARGET + b") HTTP/" + VERSION + b"$")
HTTP_HEADER_LINE  = re.compile(b"^(" + TOKEN + b"):\\s*(" + FIELD_VALUE + b")\\s*$")

# Intermediaries
@dataclass
class Declaration:
    method:  str | None
    uri:     str | None
    version: str | None

@dataclass
class Response:
    status_code: int
    body: bytes
    headers: dict[str, str]

# Exceptions
class HTTPException(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code, self.message = status_code, message

# Shadow objects
class Request:
    def __init__(self, source: tuple[str, int]) -> None:
        self.declaration: Declaration = Declaration(None, None, None)
        self.headers: dict[str, str] = {}
        self.source: tuple[str, int] = source

        self._body: bytes = b""

    def consume(self, line: bytes) -> None:
        processed_line = line[:-2]
        if self.declaration.method is None:
            if processed_line == "PRI * HTTP/2.0":
                raise HTTPException(505, "Shadow does not support HTTP/2.")

            declaration = HTTP_REQUEST_LINE.match(processed_line)
            if declaration is None:
                raise HTTPException(400, "Malformed HTTP declaration was sent.")

            self.declaration = Declaration(*(_.decode() for _ in declaration.groups()))
            return

        # Parse headers
        header_data = HTTP_HEADER_LINE.match(processed_line)
        if header_data is None:
            raise HTTPException(400, f"Malformed HTTP header line was sent: {processed_line}")

        name, value = header_data.groups()
        self.headers[name.lower().decode()] = value.decode()

    @property
    def body(self) -> bytes:
        return self._body

    def _set_body(self, body: bytes) -> None:
        self._body = body

class Shadow:
    def __init__(self, on_request: typing.Callable) -> None:
        self.on_request = on_request

    @staticmethod
    def error(status_code: int, message: str) -> Response:
        return Response(status_code, message.encode(), {"content-type": "text/plain", "connection": "close"})

    @staticmethod
    def dump_response(response: Response) -> bytes:
        return b"\r\n".join([
            f"HTTP/1.1 {response.status_code}".encode(),
            *[
                f"{name.lower()}: {value}".encode()
                for name, value in (response.headers | {
                    "content-length": str(len(response.body)),
                    "server": __version__
                }).items()
            ],
            b"\r\n" + response.body
        ])

    @staticmethod
    async def read_exact(stream: asyncio.StreamReader, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = await stream.read(size - len(data))
            if not chunk:
                raise ConnectionError("Client disconnected!")

            data += chunk

        return data

    @staticmethod
    async def send_websocket_frame(stream: asyncio.StreamWriter, opcode: int, data: bytes) -> None:
        frame = bytearray()
        frame.append(0x80 | opcode)

        # Length indication to client
        length = len(data)
        if length < 126:
            frame.append(length)

        elif length < 65536:
            frame.append(126)
            frame += struct.pack(">H", length)

        else:
            frame.append(127)
            frame += struct.pack(">Q", length)

        frame.extend(data)
        stream.write(frame)

    @staticmethod
    async def read_websocket_frame(stream: asyncio.StreamReader) -> tuple[int, bytes]:
        message = bytearray()
        while True:
            first, second = await Shadow.read_exact(stream, 2)

            # Parsing
            masked, length = second >> 7, second & 0x7F
            if masked != 1:
                raise ValueError("An unmasked client frame was received!")

            # Extended payloads
            if length == 126:
                length = struct.unpack(">H", await Shadow.read_exact(stream, 2))[0]

            elif length == 127:
                length = struct.unpack(">Q", await Shadow.read_exact(stream, 8))[0]

            # Masking
            mask = await Shadow.read_exact(stream, 4)
            encoded = await Shadow.read_exact(stream, length)

            # Accumulation
            message.extend(b ^ mask[i % 4] for i, b in enumerate(encoded))
            if first >> 7:
                break

        return first & 0x0F, bytes(message)

    async def handle_connection(self, read_stream: asyncio.StreamReader, write_stream: asyncio.StreamWriter) -> None:
        source = write_stream.get_extra_info("peername")[:2]

        # Connection loop
        try:
            while read_stream:
                request, response = Request(source), None

                # Feed data into request from client
                async for item in read_stream:
                    if item == b"\r\n":
                        break

                    request.consume(item)

                # If the stream is an EOF after reading a request,
                # assume the connection is now dead, so kill it.
                if read_stream.at_eof():
                    break

                connection = request.headers.get("connection")

                # Handle websockets
                upgrade = request.headers.get("upgrade")
                if connection and "Upgrade" in connection and upgrade == "websocket":
                    websocket_key = request.headers.get("sec-websocket-key")
                    websocket_version = request.headers.get("sec-websocket-version")

                    if not (websocket_key and websocket_version):
                        raise HTTPException(400, "WebSocket handshake failed.")

                    # Calculate accept hash
                    accept_value = b64encode(sha1(f"{websocket_key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode()).digest())
                    write_stream.write(self.dump_response(Response(
                        101,
                        b"",
                        {
                            "Upgrade": "websocket",
                            "Connection": "Upgrade",
                            "Sec-WebSocket-Accept": accept_value.decode()
                        }
                    )))

                    # Begin loop
                    while True:
                        opcode, data = await self.read_websocket_frame(read_stream)
                        match opcode:
                            case 0x8:
                                break

                            case 0x1:
                                print(data.decode())
                                await Shadow.send_websocket_frame(write_stream, 1, b"greetings")
                            
                            case 0x2:
                                print(data)

                            case 0x9:
                                await Shadow.send_websocket_frame(write_stream, 0xA, data)

                    break

                # Check for data
                content_length = request.headers.get("content-length")
                if content_length is not None:
                    if not content_length.isnumeric():
                        raise HTTPException(400, "Invalid content length provided.")

                    request._set_body(await read_stream.read(int(content_length)))

                # Fetch response
                response = await self.on_request(request)
                if response is not None:
                    response.headers |= {"connection": "close" if connection == "close" else "keep-alive"}
                    write_stream.write(self.dump_response(response))

                # If we get told to close, then terminate
                # after sending off our previous response
                if connection == "close":
                    break

                await write_stream.drain()

        except HTTPException as k:
            write_stream.write(self.dump_response(self.error(k.status_code, k.message)))
            await write_stream.drain()

        except ConnectionResetError:
            return

        # Clean up
        write_stream.close()
        await write_stream.wait_closed()

    async def serve(self, host: str, port: int) -> None:
        async with await asyncio.start_server(self.handle_connection, host, port) as http:
            await http.serve_forever()
