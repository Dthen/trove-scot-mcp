"""Pure httpx-form query encoder.

Reproduces the exact query-segment output of ``httpx 0.28``'s
``Request(..., params=params).url.raw_path`` for the value types this
repo sends (str, int-coerced-to-str upstream, lists).

Used by the upcoming stdlib-only client path (T05) — kept separate from
``client.py`` so the encoder can be tested in isolation against the
golden wire fixtures without spinning up an HTTP stack.

Contract (verified byte-equal against ``golden/legacy-requests.json``):
- dict insertion order preserved (no sorting);
- list/tuple value → repeated bare key (``k=v1&k=v2``, no ``[]`` suffix);
- percent-encoding per httpx form rules: space → ``+``;
  RFC3986 unreserved set (``A-Za-z0-9-._~``) literal; ``%XX`` uppercase hex
  for everything else; non-ASCII → UTF-8 bytes each ``%XX``;
- ``=`` joins key/value; ``&`` joins pairs; no leading ``?``.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote_plus


def encode_query(params: Mapping[str, Any]) -> str:
    """Encode *params* into an httpx-form query string (no leading ``?``).

    Args:
        params: Mapping of query keys to values. ``list``/``tuple`` values
            expand into repeated bare keys (``k=v1&k=v2``). Other values
            are coerced to ``str`` upstream before being passed in.

    Returns:
        The query segment (everything after ``?``) exactly as httpx 0.28
        would produce it — dict order preserved, percent-encoding per
        RFC3986 unreserved set, ``+`` for spaces.
    """
    pairs: list[str] = []
    for key, value in params.items():
        encoded_key = quote_plus(str(key), safe="")
        if isinstance(value, (list, tuple)):
            for item in value:
                encoded_value = quote_plus(str(item), safe="")
                pairs.append(f"{encoded_key}={encoded_value}")
        else:
            encoded_value = quote_plus(str(value), safe="")
            pairs.append(f"{encoded_key}={encoded_value}")
    return "&".join(pairs)
