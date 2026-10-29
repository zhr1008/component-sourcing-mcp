"""Protocol boundary for legacy and new tools, without changing Python call sites."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import weakref

from fastmcp.exceptions import NotFoundError, ToolError, ValidationError
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult
from mcp.shared.exceptions import MCPError

from .errors import SourcingError, error_payload

logger = logging.getLogger(__name__)


def _sourcing_cause(error: Exception) -> SourcingError | None:
    """Recover only our public domain errors from FastMCP's wrapper chain.

    Do not expose arbitrary exception messages or traverse unrelated contexts.
    FastMCP's masked ToolError retains its original cause via ``raise ... from``.
    """
    seen: set[int] = set()
    while isinstance(error, ToolError) and id(error) not in seen:
        seen.add(id(error))
        cause = error.__cause__
        if not isinstance(cause, Exception):
            return None
        error = cause
    return error if isinstance(error, SourcingError) else None


class SourcingMiddleware(Middleware):
    def __init__(self):
        self._write_locks = weakref.WeakKeyDictionary()

    async def on_call_tool(self, context, call_next):
        try:
            batch_caps = {
                "szlcsc_altium_smoke_test": 5,
                "szlcsc_compare_parts": 20,
                "szlcsc_altium_import_batch": 50,
                "szlcsc_library_add": 100,
                "szlcsc_download_parts": 100,
                "szlcsc_sync_datasheets": 200,
            }
            for key in ("identifiers", "paths"):
                values = (context.message.arguments or {}).get(key)
                if isinstance(values, list) and (
                    not values
                    or len(values) > batch_caps.get(context.message.name, 500)
                    or any(not isinstance(v, str) or not v.strip() for v in values)
                ):
                    return self._error(
                        f"{key} must contain nonempty strings within the tool batch limit",
                        "INVALID_ARGUMENT",
                    )
            # File/manifest/CAD tools historically share .tmp/.part filenames.
            # Serialize mutations within a server; concurrent lookups stay free.
            name = context.message.name
            read_only = {
                "component_providers",
                "component_search",
                "component_get",
                "component_offers",
                "szlcsc_search",
                "szlcsc_parametric_search",
                "szlcsc_get_part",
                "szlcsc_get_stock",
                "szlcsc_get_pricing",
                "szlcsc_compare_parts",
                "szlcsc_find_alternatives",
                "szlcsc_healthcheck",
                "szlcsc_parse_barcode",
                "szlcsc_jlcpcb_search",
                "szlcsc_jlcpcb_categories",
            }
            if name in read_only:
                result = await call_next(context)
            else:
                loop = asyncio.get_running_loop()
                lock = self._write_locks.setdefault(loop, asyncio.Lock())
                async with lock:
                    result = await call_next(context)
        except (NotFoundError, MCPError):
            raise  # Unknown tools / malformed protocol messages belong to JSON-RPC.
        except ValidationError:
            return self._error(
                "Arguments do not match the tool schema", "INVALID_ARGUMENT"
            )
        except SourcingError as exc:
            return ToolResult(structured_content=error_payload(exc), is_error=True)
        except Exception as exc:
            upstream = _sourcing_cause(exc)
            if upstream is not None:
                return ToolResult(
                    structured_content=error_payload(upstream), is_error=True
                )
            incident = uuid.uuid4().hex[:12]
            logger.error(
                "Tool %s failed (%s, %s)",
                context.message.name,
                incident,
                type(exc).__name__,
            )
            return self._error(
                f"Tool failed; diagnostic id: {incident}", "INTERNAL_ERROR"
            )

        # Preserve the text payload for existing clients, and add structuredContent
        # for LLM clients. A JSON string containing an error is a tool failure.
        if (
            len(result.content) == 1
            and getattr(result.content[0], "type", None) == "text"
        ):
            try:
                payload = json.loads(result.content[0].text)
            except (ValueError, TypeError):
                return result
            if isinstance(payload, dict):
                failed = bool(payload.get("error")) or payload.get("ok") is False
                if failed and isinstance(payload.get("error"), str):
                    payload = {**payload, **error_payload(payload["error"])}
                return ToolResult(
                    content=json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                    structured_content=payload,
                    is_error=failed or result.is_error,
                )
            if isinstance(payload, list):
                return ToolResult(
                    content=result.content,
                    structured_content={"results": payload, "count": len(payload)},
                    is_error=result.is_error,
                )
        return result

    @staticmethod
    def _error(message: str, code: str):
        return ToolResult(
            structured_content=error_payload(message, code=code), is_error=True
        )
