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
INDEX_DOT_HTML = """
<!doctype html>
<html lang = "en">
    <head>
        <meta charset = "utf-8">

        <!-- CSS -->
        <style>
            * {
                color: #fff;
                font-family: monospace;
            }
            body {
                background: #000;
                display: flex;
                flex-direction: column;
                align-items: center;
            }
            span {
                width: 25%;
                text-align: right;
                font-weight: bold;
            }
            article {
                display: flex;
                align-items: center;

                & > div {
                    width: 75%;
                    height: 15px;
                }
            }
            div.block {
                height: 15px;
                background: #fff;
                -webkit-mask-image: linear-gradient(to left, transparent 0, black 20px, black 100%);
                mask-image: linear-gradient(to left, transparent 0, black 20px, black 100%);
            }
            div.column {
                width: 50%;
                display: flex;
                flex-direction: column;
                gap: 5px;
            }
            h2 {
                width: 50%;
            }
        </style>

        <title>iiPython Ghost</title>
    </head>
    <body>
        <script type = "module">
            const response = await (await fetch("/stats")).json();

            // Setup columns
            for (const domain in response) {
                const column = document.createElement("div");
                column.innerHTML = `<h2>${domain}</h2>`;
                column.classList.add("column");
                document.querySelector("body").appendChild(column);

                // Setup page hits
                const max = Math.max(...Object.values(response[domain]));
                for (const path of Object.keys(response[domain]).sort((a, b) => response[domain][b] - response[domain][a])) {
                    const value = response[domain][path];
                    const article = document.createElement("article");
                    article.innerHTML = `
                        <div>
                            <div class = "block" style = "width: ${(value / max) * 100}%;"></div>
                        </div>
                        <span>${path} (${value})</span>
                    `;
                    column.appendChild(article);
                }
            }
        </script>
    </body>
</html>
""".encode()

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
            return Response(200, INDEX_DOT_HTML, {"content-type": "text/html"})

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
