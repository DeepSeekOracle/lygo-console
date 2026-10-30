"""One place that knows how to refuse a request body (defect 27, 2026-09-20).

A server that refuses an oversized body and closes without reading it resets whatever the caller is
still writing, so the caller loses the answer: the console's `/api/session` answered 413 while 600 kB
was in flight, and the caller got a network error instead of the reason. The portal's own paste path
would have shown the operator "the network is down" for a paste that was merely too large.

Both HTTP surfaces here refuse bodies — the kernel in `server.py` and the internet-facing
`public_gateway.py` — so the policy lives here instead of twice with two sets of numbers that could
drift apart.

Bounded twice, because a declared Content-Length is the caller's word and not ours:

* `CAP` bytes are discarded at most, so a caller who declares 100 GB does not cost us 100 GB;
* `TIMEOUT` seconds are spent waiting for what has not arrived yet, so a caller cannot park a
  handler thread by declaring a body and then never sending it.
"""

from __future__ import annotations

import typing as t

SIGNATURE = "Δ9Φ963-LYGO-HTTP-BODY-v1"

CAP = 1 << 20      # 1 MiB: more than any refusal we make needs, and a hard ceiling on the cost
TIMEOUT = 2.0      # seconds to wait for the part of a refused body that has not arrived yet
CHUNK = 65_536


def drain(handler: t.Any, declared: int) -> int:
    """Consume a body `handler` is refusing. Returns how many bytes were discarded.

    `handler` is a `BaseHTTPRequestHandler` — anything with `.rfile` and `.connection`. The socket
    timeout is set for the drain and put back afterwards, so a caller who stops mid-write costs the
    handler `TIMEOUT` seconds and no more.
    """
    try:
        want = min(int(declared), CAP)
    except (TypeError, ValueError):
        return 0
    if want <= 0:
        return 0
    sock = getattr(handler, "connection", None)
    saved, timed = None, False
    if sock is not None:
        try:
            saved = sock.gettimeout()
            sock.settimeout(TIMEOUT)
            timed = True
        except OSError:
            timed = False
    got = 0
    try:
        while got < want:
            chunk = handler.rfile.read(min(CHUNK, want - got))
            if not chunk:
                break
            got += len(chunk)
    except (OSError, ValueError):
        # A caller who stops mid-write is not an error: this body is being discarded anyway.
        pass
    finally:
        if timed:
            try:
                sock.settimeout(saved)
            except OSError:
                pass
    return got
