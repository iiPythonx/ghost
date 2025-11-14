# Copyright (c) 2025 iiPython

# Modules
import asyncio
from shadow import Request, Response, Shadow

# Request handling
async def on_request(request: Request) -> Response:
    if request.declaration.uri == "/":
        return Response(200, b"...<script>navigator.sendBeacon('/hi', window.location.href)</script>", {"content-type": "text/html"})

    print(request.headers, request.body)
    return Response(204, b"", {})

# Launching
if __name__ == "__main__":
    asyncio.run(Shadow(on_request).serve("localhost", 8000))
