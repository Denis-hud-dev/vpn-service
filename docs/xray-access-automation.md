# Xray access automation (local implementation)

This branch adds a **separate** subscription API, PostgreSQL intent/acknowledgement
state, and a per-node Xray agent. It does not change the running Telegram bot,
DigitalOcean, or any existing VPN listener. The local Xray 26.3.27 REALITY +
Vision data-plane test now passes, but deployment still requires a real-node and
target-network test.

## Data flow

1. A trusted payment verifier records a confirmed provider event. The existing
   lifecycle service activates or extends the subscription idempotently.
2. The subscription API worker examines paid entitlements every 30 seconds. It
   records a monotonically increasing desired revision in PostgreSQL for every
   enabled node, then sends a short lease to the node over mutually authenticated
   HTTPS. A late or duplicate request cannot overwrite a newer revision.
3. The node stores the lease in a private SQLite database **before** changing
   Xray through its loopback-only HandlerService API. It compares the actual
   managed user list with valid leases every five seconds. A lease lasts at most
   120 seconds and expires locally even when the central server is unavailable.
4. PostgreSQL records the applied lease only after the node confirms Xray state.
   The Happ feed advertises only confirmed, unexpired credentials. One HTTPS
   token identifies a subscription; adding/removing nodes changes its feed
   without changing the link. Extending a subscription retains its node UUIDs.
5. Blocked or expired subscriptions produce revocation intents. Node outages
   leave those intents pending for retry. The short lease is the fallback that
   prevents indefinitely active access when the central service is unavailable.

The bot's tariff buttons still do **not** create real payments or send links.
There is no automatic access from a button press or from a `pending` payment.
The payment verifier and link delivery need separate integration and tests.

## Trust boundaries and configuration

Use a dedicated Xray inbound, initially with no static clients. Enable its
`HandlerService` API on `127.0.0.1` only. Pin the Xray build and check the
configuration with `xray run -test` before introducing the agent. The agent
must reach that API in the **same network namespace**; never publish the API
port. Run the agent as a dedicated non-root user, bind its mTLS HTTPS listener
to a private interface, and allow only the central server to reach it. Use a
separate private CA per node, a node server certificate with the correct DNS
name, and a central client certificate signed by that node's CA. Restrict
certificate and key files to the service account (keys mode `0600`). Store all
of them outside Git and Docker build context. Do not reuse the Telegram token
or a VPN/REALITY key for management.

Set these environment variables **only for the separate processes**:

| Process | Variables |
| --- | --- |
| central subscription API | `DATABASE_URL`, `SUBSCRIPTION_PUBLIC_URL`, `SUBSCRIPTION_TLS_CERT`, `SUBSCRIPTION_TLS_KEY`, `NODE_AGENT_CONFIG`, optional `SUBSCRIPTION_PORT` |
| per-node agent | `NODE_AGENT_STATE`, `XRAY_BINARY`, `XRAY_API_ADDRESS`, `XRAY_INBOUND_TAG`, `XRAY_INBOUND_PORT`, `NODE_AGENT_CERT`, `NODE_AGENT_KEY`, `NODE_AGENT_CLIENT_CA`, optional `NODE_AGENT_LISTEN`, `NODE_AGENT_PORT` |

`SUBSCRIPTION_PUBLIC_URL` must be a public HTTPS **origin** with a certificate
trusted by Happ. The current process binds to loopback; a reverse proxy can
terminate public HTTPS and forward to it, but must not log `/s/<token>` or
request/response bodies. The application disables access logs and sets
`Cache-Control: no-store, private`. An operator must also disable URL capture in
load-balancer, CDN, tracing, and error-reporting systems.

`NODE_AGENT_CONFIG` is a private `0600` JSON file whose keys are `vpn_nodes.id`
UUIDs and whose values contain `url`, `ca`, `cert`, `key` paths. Example with
placeholders only:

```json
{
  "00000000-0000-0000-0000-000000000001": {
    "url": "https://node-1.internal.example:19443",
    "ca": "/private/node-1/client-ca.pem",
    "cert": "/private/node-1/client.crt",
    "key": "/private/node-1/client.key"
  }
}
```

The central worker must run as **one active instance**. Its gateway config is
trusted operator input, never user input. The agent takes a local process lock
and rejects stale lease revisions. Back up its SQLite state and certificate
material encrypted, with access restricted to operators. If state is lost, the
agent removes all `kuda-*` users it sees; it cannot remove unrelated Xray users.
Do not restore an old lease database while Xray remains live: stop just that
node's agent and inbound, restore verified current state, then reconcile.

## Rollout and rollback

1. Back up PostgreSQL and the dedicated Xray configuration. Verify a restore
   on an isolated instance. Apply Alembic revision `0005_node_lease_intents`.
2. Prepare one disposable node with its private CA, Xray loopback API, empty
   dedicated inbound, and agent. Test mTLS rejection without a certificate,
   add/remove, lease expiry, REALITY + Vision traffic, and a blocked account.
3. Introduce nodes one at a time. Confirm the external address, DNS, SNI,
   REALITY public key/short ID, and real Happ import on the target networks.
4. Start the separate subscription API worker. Watch reconciliation errors,
   lease age, node reachability, and feed responses. Deploying the worker does
   not start the Telegram polling bot.
5. On a fault, stop the subscription API worker to prevent new writes. Disable
   the affected node in the DB only through a reviewed operational change;
   allow its leases to expire or stop that node's dedicated Xray inbound. Do
   not roll back the additive migration while this worker is running. Restore
   from the backups only after validating the restore in isolation.

## Verification and current limit

Automated tests use disposable PostgreSQL and a real local Xray 26.3.27 API.
They cover confirmed activation, duplicate provider event, renewal, block,
revocation, node outage and recovery, local lease expiry, mTLS rejection,
and actual add/list/remove. Both plain VLESS and REALITY + Vision probes transfer
HTTPS through Xray to a local origin. The REALITY probe uses a public, trusted
camouflage target (`www.cloudflare.com`) for its outer handshake; the application
origin and Xray listener are local. The test therefore does require external
DNS/TLS reachability, but it never connects to production infrastructure.
The local origin uses a self-signed certificate; `curl --insecure` applies only
to that inner origin. REALITY still verifies its server against the configured
public key and SNI.

The earlier `SSL connection timeout` came from the probe's two-second
`curl --connect-timeout`, not from failed VLESS authentication. On this Mac,
the initial REALITY HTTPS probe completed in 5.65–5.75 seconds and returned
HTTP 200. Xray 26.3.27 pins `xtls/reality` revision `9234c772ba8f`, whose
server handshake code includes a five-second wait when it has no cached
post-handshake record lengths. The test now allows 12 seconds to connect and
18 seconds total, while still failing if no HTTP response arrives. It also
checks that a newly attempted connection fails after `RemoveUser`.

No mobile or real VPS traffic has been proven by these tests.
Furthermore, Xray `RemoveUser` was checked for rejecting new sessions; whether
already established tunnels terminate immediately requires explicit validation
on a real node. Do not claim immediate session termination until that is shown.

Run local tests with trusted `TEST_XRAY_BINARY` and a disposable PostgreSQL URL;
both data-plane probes run automatically when the binary is provided.
Never point test database variables at production.

Protocol integration follows the official [Xray API](https://xtls.github.io/en/config/api.html),
[VLESS](https://xtls.github.io/en/config/inbounds/vless.html), and
[REALITY](https://xtls.github.io/en/config/transports/reality.html) documentation.
The measured five-second wait is in the [pinned REALITY source](https://github.com/XTLS/REALITY/blob/9234c772ba8f/tls.go#L2617).
