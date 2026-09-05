# FANS-C Local Development — HTTPS for Webcam / Liveness Testing

This guide covers **local development** only — i.e. running the code from
source via `python manage.py runserver`. **End users who installed FANS-C
via the installer (`FANS-C-Setup-v2.1.13.exe` or similar) do not need any
of this.** The installer already runs Waitress behind Caddy on
`https://fans-barangay.local` with a locally trusted certificate, and the
webcam / liveness flow works out of the box.

If you are not modifying the source, stop here and use the installer.

---

## 1. Why this guide exists

Browsers only expose camera APIs (`getUserMedia`, FaceMesh via WASM,
MediaPipe) in a **secure context**. A secure context is either:

- `https://...` with a certificate the browser trusts, **or**
- a "localhost" origin: `http://localhost:<port>` or `http://127.0.0.1:<port>`.

Anything else — including `http://192.168.x.x:8000`, `http://<your-pc-name>:8000`,
or any other plain-HTTP LAN URL — is treated as **insecure**. The webcam
preview will not appear, the FaceMesh worker will refuse to start, and
liveness will report "Camera unavailable" or "secure context required".

Django's built-in development server (`python manage.py runserver`) speaks
HTTP only — it has no TLS support. So:

| Goal | URL | Works? |
|---|---|---|
| Code on the SAME laptop, browse from the SAME laptop | `http://127.0.0.1:8000/` | ✅ Yes (localhost-secure-context exemption) |
| Code on the SAME laptop, browse from the SAME laptop | `http://localhost:8000/` | ✅ Yes (localhost-secure-context exemption) |
| Code on a server, browse from another LAN device over IP/HTTP | `http://192.168.1.50:8000/` | ❌ Browser blocks camera |
| Code on a server, browse from another LAN device over HTTPS via Caddy | `https://fans-barangay.local` | ✅ Yes |
| End user using the installer | `https://fans-barangay.local` | ✅ Yes (installer-managed) |

**Rule of thumb for dev:**
- Same-laptop dev → use `runserver` + `http://127.0.0.1:8000/` and you're fine.
- LAN / multi-device dev → put Caddy in front of `runserver`. Plain LAN
  HTTP is **not supported** for the camera / liveness flow.

---

## 2. Same-laptop dev (simplest path)

This is the recommended flow during normal feature work.

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py runserver 127.0.0.1:8000
```

Open `http://127.0.0.1:8000/` (or `http://localhost:8000/`). Camera and
liveness work because the browser treats localhost as a secure context.

No certificate, no hosts file, no Caddy needed.

> ⚠️ **Do NOT switch to your LAN IP** for camera testing. `python manage.py
> runserver 0.0.0.0:8000` will start fine and you can reach it from a
> phone, but the moment you click "Capture & Verify" the camera will
> refuse to open. The fix is HTTPS via Caddy (next section), not a
> different `runserver` flag.

---

## 3. LAN / multi-device dev with HTTPS via Caddy

Use this when you need to:
- Test the camera flow on a phone / tablet pointed at a dev server PC.
- Test the full Waitress-style HTTPS reverse-proxy stack locally.
- Reproduce a bug that only shows on HTTPS (CSRF redirects, Strict-Transport-Security, etc.).

### 3.1 Prerequisites

These are already part of a normal dev setup. If you ran the
`setup-secure-server.ps1` script, you have all four.

- **mkcert** at `tools\mkcert\mkcert.exe` (download
  https://github.com/FiloSottile/mkcert/releases if missing).
- **Caddy** as a `caddy.exe` reachable on `PATH` or at a known path
  (single binary; https://caddyserver.com/download).
- Project's local certificate files in the project root:
  `fans-cert.pem` and `fans-cert-key.pem`.
- The hostname `fans-barangay.local` mapped to `127.0.0.1` in the
  Windows hosts file.

### 3.2 Generate the local certificate (first time only)

mkcert installs a per-user root CA into the Windows trust store, then
issues a leaf certificate for `fans-barangay.local`:

```powershell
.\tools\mkcert\mkcert.exe -install
.\tools\mkcert\mkcert.exe -cert-file fans-cert.pem -key-file fans-cert-key.pem fans-barangay.local localhost 127.0.0.1
```

The first command needs admin (`-install` writes to the system trust
store). The second writes the two `.pem` files into the project root
that Caddy reads.

### 3.3 Add the hosts entry (first time only)

Open an **admin** PowerShell window and append the mapping:

```powershell
Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "`r`n127.0.0.1 fans-barangay.local"
```

Or use `repair-hosts.ps1` if you ran the setup script:

```powershell
.\scripts\admin\repair-hosts.ps1
```

Verify:

```powershell
Get-Content C:\Windows\System32\drivers\etc\hosts | Select-String fans-barangay
```

Output should include `127.0.0.1 fans-barangay.local`.

### 3.4 Configure `.env` for HTTPS dev

The minimum additions on top of a normal dev `.env`:

```
ALLOWED_HOSTS=fans-barangay.local,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://fans-barangay.local
SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https
USE_X_FORWARDED_HOST=True
```

If you are testing from another LAN device, add that device's view of
the server's hostname / IP to `ALLOWED_HOSTS` as well — but the URL the
browser visits must still be `https://fans-barangay.local`, not the raw
IP, otherwise the certificate name won't match.

### 3.5 Run Django + Caddy (two terminals)

**Terminal 1 — Django runserver bound to loopback:**

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py runserver 127.0.0.1:8000
```

**Terminal 2 — Caddy as HTTPS reverse proxy:**

```powershell
caddy run --config Caddyfile
```

Open `https://fans-barangay.local` in your browser.

> Caddy reads `Caddyfile` from the current working directory, so run
> Caddy from the project root. The Caddyfile already terminates HTTPS
> on `:443` and proxies to `127.0.0.1:8000`.

### 3.6 Trust the certificate

If the browser shows a red lock or "Your connection is not private":

1. **Same machine where you ran `mkcert -install`**: close all browser
   windows and reopen — Chrome / Edge sometimes cache the untrusted
   verdict. If it still shows red, re-run `mkcert -install` in an admin
   shell.
2. **Different LAN device** (phone, tablet, another PC): copy
   `tools\mkcert\rootCA.pem` to that device and install it as a
   "trusted root certificate authority". On Android it lives under
   *Settings → Security → Encryption & credentials → Install a
   certificate → CA certificate*. On iOS, AirDrop the `.pem`, accept
   the profile, then enable it under *Settings → General → About →
   Certificate Trust Settings*.
3. As a temporary workaround while testing locally, you can click
   through the warning — Chrome lets you type `thisisunsafe` on a
   warning page. The camera will still work because the page is now
   served over HTTPS. **Do not do this in production.**

### 3.7 Stop everything

Ctrl+C in each terminal. Or:

```powershell
.\scripts\admin\stop-fans.ps1
```

---

## 4. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Camera preview never appears, console says `getUserMedia is not a function` or `secure context required` | Page is HTTP and not localhost. | Use `http://127.0.0.1:8000/` for same-laptop dev, or front `runserver` with Caddy on `https://fans-barangay.local` for LAN dev. |
| `ERR_CONNECTION_REFUSED` on `https://fans-barangay.local` | Caddy isn't running, or it can't bind :443. | Run `caddy run --config Caddyfile` from the project root in an admin shell. Verify Windows Firewall allows inbound :443. |
| Red lock / "Your connection is not private" | Browser doesn't trust the mkcert root CA. | Run `mkcert -install` (admin). On other devices, install `rootCA.pem` as a trusted CA. |
| `DisallowedHost` from Django | `fans-barangay.local` not in `ALLOWED_HOSTS`. | Add it to `.env` and restart Django. |
| CSRF verification failed on form POST | Missing `CSRF_TRUSTED_ORIGINS`. | Set `CSRF_TRUSTED_ORIGINS=https://fans-barangay.local` in `.env`. |
| Camera works on the dev laptop but not on a phone over LAN HTTP | Phone is hitting `http://<lan-ip>:8000` — not a secure context. | Phone must browse to `https://fans-barangay.local` instead (and trust the cert). Raw LAN HTTP is **not supported** for the camera flow. |
| `502 Bad Gateway` from Caddy | Caddy is up but Django isn't listening on `127.0.0.1:8000`. | Start `runserver` first; verify with `Test-NetConnection 127.0.0.1 -Port 8000`. |
| Liveness JS console shows MediaPipe 404s | `staticfiles/` is stale after editing static assets. | Run `python manage.py collectstatic --noinput --clear` and reload. |

---

## 5. What this guide does NOT change

- **FaceNet remains the final identity verification engine.** Nothing
  in this dev setup changes FaceNet behavior. PAD/liveness remains
  only a gate before FaceNet.
- **PAD / liveness thresholds and decision logic are unchanged.**
  Auto-verify threshold, manual-review band, low-quality forced
  manual review, PAD landmark-motion gate — all as documented in
  `CHANGELOG.md` for v2.1.13.
- **Installer HTTPS flow is unchanged.** Installer users continue to
  open `https://fans-barangay.local` on the LAN-joined PCs; no changes
  to Waitress, Caddy startup, watchdog, or certificate handling are
  introduced by this guide.

---

## 6. Quick reference card

```text
# Same-laptop dev (no HTTPS needed)
python manage.py runserver 127.0.0.1:8000
→ http://127.0.0.1:8000/

# LAN-device dev (HTTPS required)
python manage.py runserver 127.0.0.1:8000     # terminal 1
caddy run --config Caddyfile                  # terminal 2
→ https://fans-barangay.local

# End-user install (no dev setup needed)
Run FANS-C-Setup-v2.1.13.exe → https://fans-barangay.local
```
