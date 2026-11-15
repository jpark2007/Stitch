# Isolated ElevenLabs post-call rehearsal

The standalone proxy exposes only `POST /internal/channels/elevenlabs/post-call`.
It forwards the original bytes and `ElevenLabs-Signature` to the local API, which
must independently verify the signature, timestamp, replay, allowed agent and
pairing code before storing a draft. The proxy does not authenticate events itself.
It needs no provider credentials and stores/logs no webhook bodies or secrets.

Keep the rehearsal API bound to `127.0.0.1:8011`. Enable its Channels and
ElevenLabs webhook features separately with `STITCH_CHANNELS=1` and
`STITCH_CHANNELS_ELEVENLABS=1`; this proxy does not enable them or set
the backend webhook secret. Never tunnel the entire demo API: its other routes
and any known demo credentials must remain local.

From `channels/`, using Node 24 or newer:

```sh
STITCH_CHANNELS_CALL_PROXY=1 node src/call-webhook-proxy.ts
```

In another terminal, if cloudflared is installed:

```sh
cloudflared tunnel --url http://127.0.0.1:3023
```

Use the tunnel's HTTPS origin plus `/internal/channels/elevenlabs/post-call`
as the ElevenLabs post-call webhook URL. The random tunnel URL is not a secret
or authentication mechanism. Backend signature validation remains mandatory.
Stop both processes after rehearsal; a new quick tunnel may have a different URL.

Defaults: proxy binds `127.0.0.1:3023`; upstream is `http://127.0.0.1:8011`.
`CHANNELS_CALL_PROXY_PORT` changes the listening port.
`CHANNELS_CALL_PROXY_UPSTREAM` accepts only an HTTP origin with numeric loopback
host `127.0.0.1` or `[::1]`, optional port, and no credentials, path, query or fragment.
The forwarded path cannot be configured. Update the tunnel command if changing
the proxy port. Importing the module starts no server; direct execution requires
the exact opt-in `STITCH_CHANNELS_CALL_PROXY=1`.

Requests with alternate paths (including queries) or methods are rejected before
forwarding. The body limit is 256 KiB, including chunked requests, with a two-second
absolute read deadline and four concurrent requests maximum (excess gets 503).
Upstream calls have a five-second total deadline and a 4 KiB response cap.
Redirects are not followed. Only content type, signature and computed content
length are forwarded, never browser cookies or authorization. Upstream statuses
are preserved; response content and headers are replaced with a minimal status
receipt. Oversized/broken upstream responses return 502; timeouts return 504.
An upstream failure can never become a successful 200 acknowledgment.

Offline validation uses local HTTP servers only:

```sh
node --test tests/call-webhook-proxy.test.ts
npm run typecheck
```

These tests do not establish that a live call, provider webhook, pairing flow or
saved memory has succeeded. Verify those separately during an authorized rehearsal.
