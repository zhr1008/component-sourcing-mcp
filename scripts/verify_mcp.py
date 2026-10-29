"""Offline, real-process protocol acceptance for stdio/HTTP/legacy SSE.

Run with the project's virtualenv Python. No upstream component requests occur.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]


async def check(transport, mode="auto"):
    async with Client(transport, mode=mode, timeout=10) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert {
            "szlcsc_search",
            "szlcsc_discover",
            "szlcsc_get_stock",
            "szlcsc_get_pricing",
            "szlcsc_jlcpcb_search",
            "component_providers",
            "component_search",
            "component_get",
            "component_offers",
        } <= names
        assert len(tools) == 50
        valid = await client.call_tool("szlcsc_cache_status")
        assert not valid.is_error and "entries" in valid.structured_content
        invalid = await client.call_tool("szlcsc_get_part", {}, raise_on_error=False)
        assert invalid.is_error
        assert invalid.structured_content["error"]["code"] == "INVALID_ARGUMENT"
        assert await client.read_resource("szlcsc://sourcing-guide")
        assert await client.get_prompt(
            "select_domestic_component", {"requirements": "RS485 transceiver"}
        )
        providers = await client.call_tool("component_providers")
        assert not providers.is_error and providers.structured_content["count"] == 5
        unsupported = await client.call_tool("component_get", {"provider": "huaqiu", "identifier": "fixture"}, raise_on_error=False)
        assert unsupported.is_error and unsupported.structured_content["error"]["code"] == "UNSUPPORTED_PROVIDER"
        assert await client.read_resource("components://supplier-guide")
        assert await client.get_prompt("compare_supplier_offers", {"mpn": "fixture", "quantity": "10"})
        after_error = await client.call_tool("szlcsc_cache_status")
        assert not after_error.is_error
        return {
            "status": "PASS",
            "protocol_version": client.protocol_version,
            "tool_count": len(tools),
            "checks": [
                "discovery",
                "tools/list",
                "success",
                "validation_isError",
                "structuredContent",
                "resources/read",
                "prompts/get",
                "survives_error",
                "supplier_configuration",
                "unsupported_provider_isError",
                "supplier_resource_prompt",
            ],
        }


async def run(all_transports=False):
    results = {}
    with tempfile.TemporaryDirectory(prefix="szlcsc-protocol-") as temp:
        env = {
            **os.environ,
            "SZLCSC_CACHE_PATH": str(Path(temp) / "cache.sqlite3"),
            "PYTHONUTF8": "1",
        }
        for mode in ("auto", "legacy"):
            transport = StdioTransport(
                sys.executable, ["-m", "szlcsc_mcp"], env=env, cwd=str(ROOT)
            )
            results[f"stdio_{mode}"] = await check(transport, mode)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with subprocess.Popen(
            [sys.executable, "-m", "szlcsc_mcp"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=flags,
        ) as process:
            stdout, stderr = await asyncio.to_thread(
                process.communicate, b"", timeout=15
            )
            assert process.returncode == 0, stderr.decode(errors="replace")
            assert stdout == b"", "Non-protocol stdout emitted on startup/shutdown"
            results["stdio_eof"] = {
                "status": "PASS",
                "returncode": process.returncode,
                "stdout_bytes": 0,
            }
        if all_transports:
            for kind in ("http", "sse"):
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                with open(Path(temp) / f"{kind}.log", "wb") as logfile:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "szlcsc_mcp",
                            "--transport",
                            kind,
                            "--port",
                            str(port),
                        ],
                        cwd=ROOT,
                        env=env,
                        stdout=logfile,
                        stderr=logfile,
                        creationflags=flags,
                    )
                    try:
                        for _ in range(150):
                            if process.poll() is not None:
                                raise RuntimeError(
                                    f"{kind} exited with {process.returncode}"
                                )
                            try:
                                reader, writer = await asyncio.open_connection(
                                    "127.0.0.1", port
                                )
                                writer.close()
                                await writer.wait_closed()
                                break
                            except OSError:
                                await asyncio.sleep(0.1)
                        else:
                            raise TimeoutError(f"{kind} did not listen")
                        url = f"http://127.0.0.1:{port}/{'mcp' if kind == 'http' else 'sse'}"
                        results[kind] = await check(
                            url, "auto" if kind == "http" else "legacy"
                        )
                        if kind == "http":
                            results["http_legacy"] = await check(url, "legacy")
                            rejected = await asyncio.to_thread(
                                requests.post,
                                url,
                                json={},
                                headers={"Origin": "https://untrusted.example"},
                                timeout=3,
                            )
                            assert rejected.status_code == 403, (
                                rejected.status_code,
                                rejected.text[:300],
                            )
                            results["http_origin"] = {
                                "status": "PASS",
                                "http_status": rejected.status_code,
                            }
                            rejected_host = await asyncio.to_thread(
                                requests.post,
                                url,
                                json={},
                                headers={"Host": "untrusted.example"},
                                timeout=3,
                            )
                            assert rejected_host.status_code == 421
                            results["http_host"] = {
                                "status": "PASS",
                                "http_status": 421,
                            }
                    finally:
                        if os.name == "nt":
                            # venv's Windows launcher may own a child interpreter.
                            # Kill only the tree created by this acceptance probe.
                            await asyncio.to_thread(
                                subprocess.run,
                                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                creationflags=flags,
                            )
                        else:
                            process.terminate()
                        try:
                            await asyncio.to_thread(process.wait, timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            await asyncio.to_thread(process.wait, timeout=5)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-transports", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run(args.all_transports))
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
