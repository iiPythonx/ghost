# Copyright (c) 2025 iiPython

# Modules
import os
import sys
import json
import time
import asyncio
import sqlite3
from urllib.parse import urlparse
from shadow import Request, Response, Shadow

# Initialization
BLANK_RESPONSE = Response(204, b"", {})

# Database
class AsyncSQLite:
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path, check_same_thread = False)

    async def execute(self, *args, **kwargs):
        return await asyncio.to_thread(self._execute, *args, **kwargs)

    def _execute(self, *args, **kwargs):
        cur = self.conn.execute(*args, **kwargs)
        self.conn.commit()
        return cur.fetchall()

# Request handling
class Ghost:
    def __init__(self) -> None:
        self.db = AsyncSQLite("ghost.db")

        # Init the DB in an async context when needed
        self.initialized: bool = False

    async def on_request(self, request: Request) -> Response:
        if not self.initialized:
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS hits (
                    domain TEXT,
                    path   TEXT,
                    time   FLOAT
                )
            """)
            self.initialized = True

        if request.declaration.uri == "/":
            return Response(200, b"<script>navigator.sendBeacon('/hi', window.location.href);</script>", {"content-type": "text/html"})

        if request.declaration.uri == "/stats":
            results = await self.db.execute("""
                SELECT
                    domain,
                    path,
                    COUNT(*) AS hits
                FROM hits
                WHERE time >= strftime('%s', 'now', '-1 day')
                GROUP BY domain, path
                ORDER BY domain, path
            """)

            processed_results = {}
            for domain, path, hits in results:
                processed_results.setdefault(domain, {})
                processed_results[domain][path] = hits

            return Response(200, json.dumps(processed_results).encode(), {"content-type": "application/json"})

        # Start processing
        uri = urlparse(request.body.decode("utf-8"))
        if uri.netloc not in sys.argv:
            return BLANK_RESPONSE

        await self.db.execute("INSERT INTO hits VALUES (?, ?, ?)", (uri.netloc, uri.path, time.time()))

        print(f"[Hit] D = {uri.netloc} | P = {uri.path}")
        return Response(204, b"", {})

    async def start(self) -> None:
        await Shadow(self.on_request).serve(
            os.getenv("HOST", "0.0.0.0"),
            int(os.getenv("PORT", 8000))
        )

# Launching
if __name__ == "__main__":
    asyncio.run(Ghost().start())
