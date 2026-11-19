"""A plain TCP forwarder: the one way a browser on the host reaches an editor in a sandbox.

The sandbox is on a network with no route out, and a container on such a network cannot publish a
port. So this runs in its own container, on both networks, and publishes one port on the host's
loopback address. It forwards bytes and reads none of them, so the editor's own password check and
its WebSocket traffic pass through untouched. Throwaway spike code (spec 11).

    python front.py <listen-port> <target-host> <target-port>
"""

import asyncio
import sys


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(client_reader, client_writer, host: str, port: int) -> None:
    try:
        target_reader, target_writer = await asyncio.open_connection(host, port)
    except OSError as exc:
        print(f"cannot reach {host}:{port}: {exc}", flush=True)
        client_writer.close()
        return
    await asyncio.gather(pipe(client_reader, target_writer), pipe(target_reader, client_writer))


async def main(listen_port: int, host: str, port: int) -> None:
    server = await asyncio.start_server(lambda r, w: handle(r, w, host, port), "0.0.0.0", listen_port)
    print(f"forwarding 0.0.0.0:{listen_port} to {host}:{port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2], int(sys.argv[3])))
