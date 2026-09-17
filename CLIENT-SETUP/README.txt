================================================================
 FANSC  |  Client Setup Package
 Run this once on each device that needs to access FANSC.
================================================================


HOW TO RUN SETUP (3 steps)
---------------------------

  Step 1.  Make sure  rootCA.pem  is in this folder.

           WHERE TO GET rootCA.pem:
           ─────────────────────────────────────────────────────
           A) After a fresh install, FANS-C copies rootCA.pem
              here automatically on first launch.

              Copy this entire CLIENT-SETUP folder to a USB
              drive from the server install folder:
                C:\FANSC\_internal\CLIENT-SETUP\

           B) If rootCA.pem is still missing after FANS-C has
              been running, get it manually on the SERVER:
                1. Open PowerShell on the server
                2. Run:
                     & "C:\FANSC\_internal\tools\mkcert\mkcert.exe" -CAROOT
                3. Open the printed folder and copy rootCA.pem
                   to this CLIENT-SETUP folder on the USB drive.

           NOTE: NEVER copy rootCA-key.pem to client machines.
                 Only rootCA.pem is needed and safe to distribute.
           ─────────────────────────────────────────────────────

  Step 2.  Double-click:   trust-local-cert.bat

           A security prompt will appear -- click "Yes" to allow it.
           Follow the steps on screen.
           When asked for the server IP address, enter the address
           your IT admin gave you (example: 192.168.1.77).

  Step 3.  When setup says "Setup complete!", open your browser and
           go to:     https://fans-barangay.local

That's it.  You do not need to run setup again on this device.


================================================================
 THE SERVER MOVED TO A DIFFERENT WI-FI/NETWORK
================================================================

If https://fans-barangay.local suddenly stops loading after the
server PC was shut down and reconnected somewhere else (a
different Wi-Fi, a different router), the most common cause is
that this PC's saved server address is now out of date -- the
server got a new address from the new network, but this PC does
not know that yet.

Fix it without asking IT for the new address every time:

  1. Connect this PC to the SAME Wi-Fi/network as the server.
  2. Double-click:   refresh-server-connection.bat
  3. Approve the security prompt.  Wait a few seconds while it
     looks for the server on this network.
  4. Open https://fans-barangay.local again.

This only works when this PC and the server are on the same
Wi-Fi/network right now, and it only updates THIS PC -- every
other client device still needs to run it (or run
trust-local-cert.bat again with the new IP) on its own.  It does
not reach out to the Internet and does not need a new IP typed
in by hand.


================================================================
 FOR IT ADMINISTRATORS -- before distributing this package
================================================================

AFTER A FRESH INSTALL:
  FANS-C automatically copies rootCA.pem to this folder when
  the server launches for the first time.  Just copy the entire
  CLIENT-SETUP folder from the server to a USB drive:

    C:\FANSC\_internal\CLIENT-SETUP\

AFTER AN UPGRADE INSTALL:
  FANS-C also copies rootCA.pem here on every launch once the
  mkcert certificate authority exists on the server.  If rootCA.pem
  is missing after a launch, get it manually:

    1. On the SERVER, open PowerShell and run:
           & "C:\FANSC\_internal\tools\mkcert\mkcert.exe" -CAROOT
       This prints the CA folder path.

    2. Open that folder and copy   rootCA.pem   to a USB drive.

    3. Place   rootCA.pem   in this CLIENT-SETUP folder
       (same folder as trust-local-cert.bat).

    4. Copy this entire CLIENT-SETUP folder to each client device
       (via USB drive, shared folder, or email).

NEVER DISTRIBUTE:
    rootCA-key.pem   -- this is the private CA key.
                        ONLY rootCA.pem goes to clients.


WHAT THE SETUP SCRIPT DOES
---------------------------
  - Installs the FANSC server certificate so the browser trusts
    the secure HTTPS connection (no security warning).
  - Adds fans-barangay.local to the computer's address list so
    the browser can find the server by name.
  - Does NOT change any other system settings.
  - mkcert does NOT need to be installed on client devices.


TROUBLESHOOTING
---------------
rootCA.pem is missing from this folder:
  -> FANS-C copies it automatically on every server launch.
     Start FANS-C on the server, then re-copy CLIENT-SETUP from
     C:\FANSC\_internal\CLIENT-SETUP\ to the USB drive.
  -> Or get it manually: see "FOR IT ADMINISTRATORS" above.

Browser still shows a security warning after setup:
  -> Restart the browser completely and try again.
  -> Make sure rootCA.pem was copied from THIS server (not a
     different machine).

Cannot reach https://fans-barangay.local:
  -> Make sure you are connected to the same network as the server.
  -> If the server recently moved to a different Wi-Fi/network, run
     refresh-server-connection.bat in this folder first -- see
     "THE SERVER MOVED TO A DIFFERENT WI-FI/NETWORK" above.
  -> If that does not find the server, ask the IT admin for the
     correct server IP address and re-run trust-local-cert.bat.
  -> Check that the hosts file entry was added (the script does this).
  -> This is an ERR_CONNECTION_REFUSED error -- see SETUP.md on the
     server for full troubleshooting steps.

For other issues, contact your IT administrator.

================================================================
