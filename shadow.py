# Copyright (c) 2025 iiPython

# Modules
import re
import typing
import asyncio
from dataclasses import dataclass

# Initialization
UNRESERVED  = r"A-Za-z0-9\-._~"
SUB_DELIMS  = r"!$&'()*+,;="
PCHAR       = rf"{UNRESERVED}{SUB_DELIMS}:@%"
QUERY_CHARS = rf"{PCHAR}/?"

REQUEST_TARGET = rf"/[{PCHAR}/]*(?:\?[{QUERY_CHARS}]*)?"
TOKEN          = r"[!#$%&'*+\-\.^_`|~0-9A-Za-z]+"
VERSION        = r"(\d+(?:\.\d+)?)"
FIELD_VALUE    = r"[\x20-\x7E]*"

HTTP_REQUEST_LINE = re.compile(rf"^({TOKEN})\s({REQUEST_TARGET})\sHTTP/{VERSION}$")
HTTP_HEADER_LINE  = re.compile(rf"^({TOKEN}):\s*({FIELD_VALUE})\s*$")

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
        processed_line = line.decode("utf-8").removesuffix("\r\n")
        if self.declaration.method is None:
            if processed_line == "PRI * HTTP/2.0":
                raise HTTPException(505, "Shadow does not support HTTP/2.")

            declaration = HTTP_REQUEST_LINE.match(processed_line)
            if declaration is None:
                raise HTTPException(400, "Malformed HTTP declaration was sent.")

            self.declaration = Declaration(*declaration.groups())
            return

        # Parse headers
        header_data = HTTP_HEADER_LINE.match(processed_line)
        if header_data is None:
            raise HTTPException(400, f"Malformed HTTP header line was sent: {processed_line}")

        name, value = header_data.groups()
        self.headers[name.lower()] = value

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
        return Response(status_code, message.encode(), {"content-type": "text/plain"})

    @staticmethod
    def dump_response(response: Response) -> bytes:
        return b"\r\n".join([
            f"HTTP/1.1 {response.status_code}".encode(),
            *[
                f"{name.lower()}: {value}".encode()
                for name, value in (response.headers | {
                    "connection": "close",
                    "content-length": str(len(response.body)),
                    "server": "shadow"
                }).items()
            ],
            b"\r\n" + response.body
        ])

    async def handle_connection(self, read_stream: asyncio.StreamReader, write_stream: asyncio.StreamWriter) -> None:
        request, response = Request(write_stream.get_extra_info("peername")[:2]), None

        # Feed data into request from client
        async for item in read_stream:
            if item == b"\r\n":
                break

            try:
                request.consume(item)

            except HTTPException as k:
                response = self.error(k.status_code, k.message)
                break

        # Check for data
        content_length = request.headers.get("content-length")
        if content_length is not None:
            if not content_length.isnumeric():
                response = self.error(400, "Invalid content length provided.")

            else:
                request._set_body(await read_stream.read(int(content_length)))

        # Fetch response
        response = response or await self.on_request(request)
        if response is not None:
            write_stream.write(self.dump_response(response))
            await write_stream.drain()

        # Clean up
        write_stream.close()
        await write_stream.wait_closed()

    async def serve(self, host: str, port: int) -> None:
        async with await asyncio.start_server(self.handle_connection, host, port) as http:
            await http.serve_forever()
