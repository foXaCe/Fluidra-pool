# Capturing the non-EMEA Fluidra API endpoints — volunteer guide

*Draft for issue [#91](https://github.com/foXaCe/Fluidra-pool/issues/91). Not
posted yet — this is the text ready to paste, written for a non-EMEA user
(e.g. North America / iAquaLink, or APAC / Australia) willing to help.*

---

## Why this is needed

This integration only talks to Fluidra's **EMEA** backend
(`api.fluidra-emea.com`, Cognito in `eu-west-1`). If your myFluidra /
Fluidra Connect / iAquaLink account was created outside Europe, it lives on
a **different backend with different, unpublished URLs** — the official app
knows how to find it, this integration doesn't. There is no way to guess
those URLs safely (no brute-forcing, no DNS enumeration) — the only reliable
way to find them is to watch the official app's own network traffic while
it talks to your account.

This guide walks through capturing that traffic **on your own device, with
your own account** — nothing here targets systems you don't own or bypasses
protections other than your own device's own network stack.

## Prerequisites

- **Your own Android or iOS phone/tablet** with the official Fluidra
  app (iAquaLink / Fluidra Connect / myFluidra — whichever one you normally
  use to control your pool) installed and logged in.
- **A capture proxy**: [mitmproxy](https://mitmproxy.org/) (free,
  cross-platform) or [Charles Proxy](https://www.charlesproxy.com/) — both
  work by making your phone route its traffic through a small local proxy
  server on your computer.
- **Installing the proxy's CA certificate on your phone**, so it can decrypt
  HTTPS traffic for inspection. This is a standard, documented step for both
  tools — follow mitmproxy's or Charles' own instructions for your OS. This
  only affects traffic on *your* device while the proxy is active; remove
  the certificate afterward if you don't want to keep it installed.
- **Certificate pinning — unknown, please confirm first.** Some mobile apps
  refuse to trust *any* certificate except the one they ship with
  (certificate/SSL pinning), which would make the proxy's capture appear as
  connection errors in the app even with the CA certificate installed. We
  don't know yet whether the Fluidra app does this. **If you're the first
  volunteer to try this**: install the proxy, open the app, and check
  whether it logs in normally and shows pool data while the proxy is
  running. If it does — no pinning, proceed. If the app can't connect or
  shows a network error only while the proxy is active, it's likely pinned,
  and please say so in the issue — that changes what's needed next (e.g. a
  rooted/jailbroken device with an unpinning tool), and we'd rather know
  than have you spend time on a dead end.

## What to capture

Two things, from a normal login-and-browse session in the app with the
proxy running:

### 1. The login call (most important)

Look for a request to a host containing `cognito-idp` and `amazonaws.com`
— e.g. `cognito-idp.<region>.amazonaws.com`. Note:

- **The full hostname** — the `<region>` part (e.g. `us-east-1`,
  `ap-southeast-2`) is exactly what's missing today.
- **The JSON request body.** It contains a field called `"ClientId"` — a
  short alphanumeric string. **This is safe to share — it's a public app
  identifier, not a secret** (comparable to a public API key that only
  identifies *which app* is talking, not *who*; Fluidra's own EMEA app ships
  it in plain text too). Please copy the `ClientId` value into the issue.
- The body will also contain your credentials or session data in other
  fields — see the **redaction section** below before pasting anything.

### 2. Two or three API calls after login

Once logged in, browse to your pool/device list in the app (this alone is
enough to trigger the calls we need). Look for requests to a host that is
**not** `amazonaws.com` — this is Fluidra's own API host for your region
(the equivalent of `api.fluidra-emea.com`). Please capture:

- The **base hostname** of that host.
- One request whose path ends in `/generic/users/me/pools` (or similar —
  the app may call it as it loads your pool list).
- If you can find it, one request to a path like `/generic/devices` — check
  if it has `format=tree` and `deviceType=connected` in its query string
  (the EMEA app does; useful to confirm it's the same on your region).
- The **`User-Agent` request header** sent by the app on any of these calls
  — please copy it verbatim (it will look like an Android/iOS app
  identifier + OS version string).

You don't need to capture write/control calls (turning something on/off) for
this first pass — read-only browsing is enough to unblock the initial
implementation.

## What to REDACT before posting

Before pasting anything into the issue, remove or blank out:

- **`AccessToken`, `RefreshToken`, `IdToken`** — any field with these exact
  names, anywhere in a request or response body. These are your live
  session credentials.
- **Your email address** and **password** — the password appears in
  cleartext in the login request body; the email appears in several places.
- **Device/pool serial numbers** and any other identifier that could tie the
  capture back to your specific equipment, if you'd rather not share it.
- Anything else that looks like a bearer token (`Authorization: Bearer …`
  headers) — same reasoning as the tokens above.

**Safe to leave in / explicitly worth sharing:**
- The `ClientId` field (see above — it's public by design).
- Hostnames and endpoint paths.
- The `User-Agent` header.
- Response status codes and the *shape* of response JSON (field names) —
  useful to compare against what this integration already expects. If a
  response body contains no tokens/credentials, feel free to share it whole;
  otherwise redact just the sensitive fields and keep the rest.

### Example of a correctly redacted login body

```json
{
  "AuthFlow": "USER_PASSWORD_AUTH",
  "ClientId": "abcd1234exampleclientid",
  "AuthParameters": {
    "USERNAME": "[REDACTED]",
    "PASSWORD": "[REDACTED]"
  }
}
```

And a correctly redacted response fragment:

```json
{
  "AuthenticationResult": {
    "AccessToken": "[REDACTED]",
    "RefreshToken": "[REDACTED]",
    "IdToken": "[REDACTED]",
    "ExpiresIn": 3600,
    "TokenType": "Bearer"
  }
}
```

## Where to post it

Please add a comment on
[issue #91](https://github.com/foXaCe/Fluidra-pool/issues/91) with:

1. Which region/country your account is registered in, and which official
   app you used (iAquaLink / Fluidra Connect / myFluidra — exact name and
   platform).
2. The Cognito hostname + `ClientId` from the login call.
3. The API base hostname + the one or two endpoint paths you captured.
4. The `User-Agent` header value.
5. Whether you had to do anything special to get the proxy working (e.g.
   certificate pinning workaround) — even a "worked immediately, no pinning"
   note is useful.

That's enough to start building the multi-region support for your region.
Thank you — this single capture is the one thing blocking it today.
