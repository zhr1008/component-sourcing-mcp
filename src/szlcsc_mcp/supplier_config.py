"""Explicit local credential file; environment variables take precedence."""

import json
import os
from pathlib import Path


def read_supplier_settings() -> dict[str, str]:
    filename = os.getenv("COMPONENT_SUPPLIERS_CONFIG")
    if not filename:
        return {}
    fields = {
        "mouser": {"api_key": "MOUSER_API_KEY"},
        "digikey": {
            "client_id": "DIGIKEY_CLIENT_ID",
            "client_secret": "DIGIKEY_CLIENT_SECRET",
            "access_token": "DIGIKEY_ACCESS_TOKEN",
            "sandbox": "DIGIKEY_SANDBOX",
            "site": "DIGIKEY_SITE",
            "currency": "DIGIKEY_CURRENCY",
            "language": "DIGIKEY_LANGUAGE",
            "account_id": "DIGIKEY_ACCOUNT_ID",
        },
    }
    try:
        with Path(filename).expanduser().open("rb") as file:
            data = file.read(65537)
        if len(data) > 65536:
            raise ValueError("Credential file is too large")
        config = json.loads(data)
        if not isinstance(config, dict) or set(config) - set(fields):
            raise ValueError("Invalid supplier configuration object")
        result = {}
        for provider, options in config.items():
            if not isinstance(options, dict) or set(options) - set(fields[provider]):
                raise ValueError("Invalid supplier configuration fields")
            for name, value in options.items():
                if name == "sandbox" and isinstance(value, bool):
                    value = str(value).lower()
                if (
                    not isinstance(value, str)
                    or len(value) > 8192
                    or any(c in value for c in "\r\n\0")
                ):
                    raise ValueError("Invalid supplier configuration value")
                result[fields[provider][name]] = value
        return result
    except (OSError, ValueError, TypeError):
        raise ValueError(
            "Cannot read a valid local supplier configuration; review the setup guide"
        ) from None
