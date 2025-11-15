# Copyright (c) 2025 iiPython

# Modules
import re
import typing
import asyncio
from dataclasses import dataclass

__version__ = "shdw/1.1.1"

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
HTTP_HEADER_LINE = re.compile(b"^(" + TOKEN + b"):\\s*(" + FIELD_VALUE + b")\\s*$")

# Intermediaries
@dataclass
class Declaration:
    method:  bytes | None
    uri:     bytes | None
    version: bytes | None

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
        self.headers: dict[bytes, bytes] = {}
        self.source: tuple[str, int] = source

        self._body: bytes = b""

    def consume(self, line: bytes) -> None:
        processed_line = line[:-2]
        if self.declaration.method is None:
            if processed_line == b"PRI * HTTP/2.0":
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

    async def handle_connection(self, read_stream: asyncio.StreamReader, write_stream: asyncio.StreamWriter) -> None:
        source = write_stream.get_extra_info("peername")[:2]

        # Connection loop
        try:
            print("[Connection]")
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

                print("[Request]")
                keepalive = request.headers.get(b"connection") == b"keep-alive"

                # Check for data
                content_length = request.headers.get(b"content-length")
                if content_length is not None:
                    if not content_length.decode("utf-8").isnumeric():
                        raise HTTPException(400, "Invalid content length provided.")

                    request._set_body(await read_stream.read(int(content_length)))

                # Fetch response
                response = await self.on_request(request)
                if response is not None:
                    response.headers |= {"connection": "keep-alive" if keepalive else "close"}
                    write_stream.write(self.dump_response(response))

                # If we aren't sent a keep-alive, terminate
                # after sending off our previous response
                if not keepalive:
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
