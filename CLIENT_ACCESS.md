# FANSC Client Access Guide

> For barangay staff accessing the verification system.
> No installation required — just use your web browser.

---

## How to Access the System

1. Make sure your computer or tablet is connected to the **same Wi-Fi or network** as the FANSC server.

2. Open any web browser (Chrome, Edge, Firefox).

3. Go to:
   ```
   https://fans-barangay.local
   ```

4. Log in with your username and password provided by the administrator.

That's all. There is nothing to install.

---

## Requirements

| Requirement | Details |
|---|---|
| Same network | Your device must be on the same Wi-Fi or LAN as the FANSC server |
| Web browser | Chrome, Edge, or Firefox (updated version recommended) |
| Login credentials | Provided by the system administrator |

---

## The "Limited HTTP Mode" Banner

If you see a yellow or orange banner at the top of the screen that says **"Limited HTTP mode — camera verification requires secure HTTPS"**, it means the system has detected that your connection is not going through the secure HTTPS channel.

**What this means for you:**
- The site is still accessible and you can browse beneficiary records and use most features.
- Camera verification (the live face scan step) is blocked until the connection is secured.
- You cannot complete a face verification until the banner disappears.

**What to do:**

1. Make sure you are accessing the system at **`https://fans-barangay.local`** (with `https://`, not `http://`).
2. If you are accessing via `http://` instead of `https://`, close the tab and open the correct link.
3. If you are using `https://` and the banner still appears, the server may need to be restarted. Contact your IT administrator.

**Note:** Running `trust-local-cert.bat` removes the browser certificate warning (the "Your connection is not private" page). It does not fix the Limited HTTP mode banner — those are two separate issues.

---

## If You Cannot Connect

### "This site can't be reached" or "Server not found"

- The FANSC server may not be running. Contact your IT administrator or the developer to start the server.
- Make sure you are connected to the correct Wi-Fi network (not mobile data).
- Try the IP address instead: `https://192.168.1.77` (your admin will confirm the exact IP).

### Browser shows a certificate warning

This is expected on first access from a new device that has not been set up yet. The system uses a certificate issued by the FANSC server's own Certificate Authority, which the device does not yet know about.

**For staff:** Contact your IT administrator. They will run the client trust setup on your device (done once per device). After that, the padlock will appear and no warning will be shown.

**For the IT administrator:**
The client device needs to trust the server's Certificate Authority — this is not the same as generating a new certificate on the client. Specifically:

1. On the **server**, find the root CA file:
   ```
   mkcert -CAROOT
   ```
   This prints a folder path. Copy `rootCA.pem` from that folder.

2. Put `rootCA.pem` and `trust-local-cert.bat` on the client device (USB, network share, etc.).

3. On the **client device**, run as Administrator:
   ```
   trust-local-cert.bat
   ```
   This imports the server's `rootCA.pem` into Windows Trusted Root Certification Authorities using `certutil`. After that, the browser will trust the FANSC certificate.

> Do NOT run `mkcert -install` on client devices. That creates a separate CA local to the client machine, which does not sign the server's certificate and does not fix the warning.

### Domain name does not work (`fans-barangay.local` shows "Server not found")

This means the device does not know which IP address `fans-barangay.local` points to. There are two ways to fix this, depending on how the network is set up.

#### Option A — Router DNS (Recommended — no per-device work needed)

If the IT administrator has already configured the office router to map `fans-barangay.local` to the server IP, the domain should work automatically on any device connected to the office Wi-Fi. If it still does not work, contact the IT administrator to verify the router DNS entry is saved and the device is on the correct Wi-Fi network.

#### Option B — Hosts File (Fallback — for testing or when router config is not available)

If the router has not been configured with a DNS entry, the IT administrator must add the domain to each device individually.

Ask the IT administrator to do this — it requires one change to a system file and is done once per device.

**For the IT administrator:**
Add this line to `C:\Windows\System32\drivers\etc\hosts` on the client device:
```
192.168.1.77   fans-barangay.local
```
Replace `192.168.1.77` with the server's actual IP address. This must be done on each Windows PC separately. Android and iOS devices cannot use this method — they require router DNS (Option A).

#### Which method is set up here?

| Method | Who sets it up | Applies to |
|---|---|---|
| Router DNS (Option A) | IT admin — once on the router | All devices automatically |
| Hosts file (Option B) | IT admin — once per PC | Only that specific PC |

Ask your IT administrator which method is in use. If you are on a new PC and the domain does not work, Option B may need to be applied to your device.

---

## Logging In

1. Enter your **username** and **password**.
2. Click **Sign In**.
3. The dashboard will appear if login is successful.

If you see "Invalid credentials", check your username and password carefully. Passwords are case-sensitive. Contact your administrator if you need your password reset.

---

## What You Can Do in the System

| Task | How |
|---|---|
| Register a new beneficiary | Register → New Beneficiary |
| View the beneficiary list | Register → Beneficiary List |
| Run a face verification | Verify (top menu) |
| View verification logs | Logs → Verification Logs |

Your account type (Staff, Admin, IT, or President) determines which features you can access.

---

## Signing Out

Click your name in the top-right corner of the screen, then click **Sign Out**.

Always sign out when you are done, especially on shared computers.

---

## Need Help?

Contact your system administrator or the developer responsible for the FANSC deployment.

For developer setup and server configuration, see [SETUP.md](SETUP.md).

---

## Does the System Require Internet?

**No. This system does not require a public internet connection for normal daily use.**

FANSC runs entirely inside the barangay office's local network (LAN or Wi-Fi). The server is a PC inside the office, and all staff devices connect to it directly over the local network.

| Situation | What happens |
|---|---|
| Internet is down, but office Wi-Fi is working | System works normally |
| Internet is working | System also works (internet is not used) |
| Server PC is turned off | System is unavailable (see below) |
| Your device loses Wi-Fi | You cannot reach the system until Wi-Fi reconnects |

**In short:** as long as the server PC is on and you are connected to the office network, the system works — regardless of whether there is any public internet connection.

---

## What Happens When the Server PC is Turned Off?

If the server PC (the main computer running FANSC) is turned off:

- **You cannot log in.** The browser will show "This site can't be reached" or similar.
- **No verification can be processed** through the main system.
- **No data can be entered** into the central system.

This is expected. FANSC is a centralized system — everything runs on one server.

**What to do:** Contact the system administrator or IT person responsible for the server. They need to turn the server PC back on and restart the FANSC server processes. Once the server is running again, all staff can connect normally without any reinstallation.

**The system does not need to be reinstalled after a reboot.** The server just needs to be turned on and the startup scripts run. All data, registrations, and configurations are preserved.

---

## What If Your Device Temporarily Loses Connection?

If your device loses connection to the server while you are working:

- **Already submitted records are safe** — they were saved to the server before your connection dropped.
- **In-progress actions** (e.g., a verification you were in the middle of) may need to be restarted once connection is restored.
- **Wait for Wi-Fi to reconnect**, then refresh the browser and try again.

Wait for Wi-Fi to reconnect, then refresh the browser and continue. Contact the administrator if the problem persists.
