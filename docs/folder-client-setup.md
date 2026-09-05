# Folder: CLIENT-SETUP/

## Purpose

The `CLIENT-SETUP/` folder contains the script that IT runs on each staff device to configure it for access to the FANS-C system. It handles the one technical step required on client devices: trusting the FANS-C server's local HTTPS certificate.

## Why it exists

The FANS-C server generates its own HTTPS certificate using `mkcert`, a local Certificate Authority (CA) tool. This certificate is valid and properly signed, but it is signed by the server's own CA — a CA that no web browser knows about by default. Web browsers maintain a list of trusted Certificate Authorities; certificates signed by unknown CAs trigger a security warning and may block camera access.

The `trust-local-cert.bat` script solves this by installing the server's CA certificate into the Windows trust store on each staff device. Once installed, the browser automatically trusts the FANS-C certificate — no warnings, no camera blocks, no IT visits needed for future access.

## Important files inside

### CLIENT-SETUP/trust-local-cert.bat

**What it does:**
1. Locates the server's root CA certificate file (`rootCA.pem`) — the file that mkcert generated on the server
2. Imports it into the Windows Trusted Root Certification Authorities store using `certutil`
3. After import, Chrome, Edge, and Firefox on that device will automatically trust any HTTPS certificate signed by the FANS-C server's CA

**When it is used:** Once per client device. Run this before the first time a staff member uses a new device to access `https://fans-barangay.local`.

**Who runs it:** IT (must be run as Administrator — double-click the file and approve the UAC prompt).

**How to use it:**
1. On the server, run `mkcert -CAROOT` to find where `rootCA.pem` is stored
2. Copy `rootCA.pem` and the `CLIENT-SETUP/` folder to a USB drive
3. On each staff device: plug in the USB drive, double-click `trust-local-cert.bat`, approve the Administrator prompt
4. Done. No reboot required. Open Chrome or Edge and go to `https://fans-barangay.local`.

**What it does NOT do:**
- It does not install software
- It does not modify any application settings
- It does not create a new certificate or CA on the client device
- It does not run `mkcert -install` on the client (that would create a separate, unrelated CA)

**How to connect to the system:**

```
mkcert (runs on server during setup)
        |
        | generates rootCA.pem (server's Certificate Authority)
        | generates fans-cert.pem (certificate signed by that CA)
        |
        v
fans-cert.pem → used by Caddy for HTTPS
rootCA.pem    → distributed to client devices via USB + CLIENT-SETUP/

trust-local-cert.bat (runs on each client device as Admin)
        |
        | certutil -addstore -f "Root" rootCA.pem
        |
        v
Windows trust store on client device
        |
        v
Browser trusts fans-barangay.local certificate
        |
        v
No certificate warning → camera works → staff can use the system
```

---

## How it connects to the system

| System component | Connection to CLIENT-SETUP/ |
|---|---|
| mkcert (setup-secure-server.ps1) | Generates the rootCA.pem that trust-local-cert.bat installs |
| Caddy | Uses fans-cert.pem signed by the same CA that clients learn to trust |
| Browser (staff device) | Trusts the CA after trust-local-cert.bat runs; no warning on HTTPS |
| Camera access | Only works on HTTPS; HTTPS only works if certificate is trusted |
| Django CSRF protection | Requires HTTPS; only works if the certificate is trusted by the browser |

---

## Runtime flow

| Phase | How CLIENT-SETUP/ is involved |
|---|---|
| Setup | IT runs trust-local-cert.bat on each staff device once |
| Daily runtime | No involvement — certificate is already trusted |
| New device added | IT runs trust-local-cert.bat on the new device |
| Certificate regenerated | May need to re-run trust-local-cert.bat (if the CA changed) |

---

## Defense notes

**Why must this be run as Administrator?**
The Windows Trusted Root Certification Authorities store is a system-level resource. Only Administrator accounts can modify it. `certutil -addstore -f "Root"` requires elevated privileges — which is why the UAC prompt appears.

**What happens if this step is skipped?**
The browser shows a certificate security warning (ERR_CERT_AUTHORITY_INVALID or similar). In most browsers, the user can click "Advanced" and proceed anyway, but:
- The camera API (`getUserMedia`) will not work on a page that bypassed the certificate warning
- Staff cannot run face verification
- The system is functionally unusable for its primary purpose

**What is the difference between running trust-local-cert.bat on the client vs. running mkcert -install on the client?**
`mkcert -install` installs a new, locally-generated CA that is unique to the client machine. This CA is different from the server's CA and does not sign the FANS-C server's certificate. Installing the client's own CA does nothing to fix the browser warning for the server's certificate. The correct approach is always to install the **server's** CA on each client — which is what `trust-local-cert.bat` does.

**Does every browser need to be configured separately?**
No. Installing the CA into the Windows trust store (which `certutil -addstore "Root"` does) is picked up automatically by Chrome, Edge, and most Chromium-based browsers. Firefox has its own certificate store by default but can be configured to use the Windows store via a policy. If Firefox shows a warning after running trust-local-cert.bat, check Firefox's certificate settings.

**What if the server's IP address changes?**
The HTTPS certificate is issued for the domain name `fans-barangay.local`, not for an IP address. If the server IP changes but the domain still resolves to the new IP (via router DNS or an updated hosts file entry), the certificate remains valid. The client trust setup does not need to be repeated when the IP changes.

**What if the certificate expires or is regenerated?**
mkcert certificates are typically valid for several years. If `setup-secure-server.ps1` is re-run and a new certificate is generated with a different CA, the old CA installed on client devices may no longer work. In that case, re-run `trust-local-cert.bat` on each device with the new `rootCA.pem`. (If the same CA is reused but a new certificate is signed by it, clients do not need to be updated.)

---

## Related folders/files

- `tools/mkcert/mkcert.exe` — generates the rootCA.pem during setup
- `fans-cert.pem` and `fans-cert-key.pem` — the certificate that Caddy uses; signed by the server's CA
- `scripts/setup/setup-secure-server.ps1` — calls mkcert to generate the certificates
- `Caddyfile` — references `fans-cert.pem` and `fans-cert-key.pem`
- `CLIENT_ACCESS.md` — staff-facing guide explaining what to do if a certificate warning appears

---

## Summary

The `CLIENT-SETUP/` folder solves a single but critical problem: making staff device browsers trust the FANS-C server's HTTPS certificate so the camera works. It contains one script (`trust-local-cert.bat`) that installs the server's Certificate Authority into the Windows trust store. This is a one-time step per device. After it is done, staff devices need no further configuration to access the system.
