# HTTPS Setup Guide (mkcert)

## Overview

This system uses **HTTPS (Secure Access)** to enable:

* Camera-based face verification
* Secure login and sessions
* Full system functionality

To make this work on each device, the browser must **trust the certificate authority (CA)** used by the server.

We use **mkcert** for this purpose.

---

## ⚠️ Important Concepts

* The **server PC** generates the HTTPS certificate
* Other devices (laptops, desktops) only need to **trust the certificate**
* This is a **one-time setup per device**

---

## 📁 Included Tool

This project includes:

```
/tools/mkcert/mkcert.exe
```

---

## 🖥️ STEP 1 — Server PC Setup (ONLY ONCE)

On the **server PC**:

### 1. Open PowerShell in tools/mkcert

```
cd tools\mkcert
```

### 2. Install mkcert CA

```powershell
.\mkcert.exe -install
```

### 3. Generate certificate for the system

```powershell
.\mkcert.exe fans-barangay.local
```

This will generate files like:

```
fans-barangay.local.pem
fans-barangay.local-key.pem
```

These are used by Caddy.

---

## 💻 STEP 2 — Client Devices (Laptop / Other PCs)

On **each device that will access the system**:

### 1. Open PowerShell

Navigate to mkcert:

```powershell
cd tools\mkcert
```

### 2. Install mkcert CA

```powershell
.\mkcert.exe -install
```

### 3. Restart browser

Close all browsers and open again.

---

## 🌐 STEP 3 — Access the System

Open:

```
https://fans-barangay.local
```

### Expected:

* No "Not Secure" warning
* System loads correctly
* Camera works

---

## ❗ Important Notes

* Only the **server PC runs**:

  ```
  mkcert fans-barangay.local
  ```
* Other devices DO NOT generate certificates
* They only run:

  ```
  mkcert -install
  ```

---

## 🔐 Security Reminder

Do NOT share or upload the following files:

```
rootCA.pem
rootCA-key.pem
```

These are private security files.

---

## 🧪 Troubleshooting

### Camera not working

Make sure you are using:

```
https://fans-barangay.local
```

NOT:

```
http://192.168.x.x:8000
```

---

### "Not Secure" warning

* Run:

  ```powershell
  .\mkcert.exe -install
  ```
* Restart browser

---

### Cannot access domain

* Check Wi-Fi connection (same network)
* Verify hosts file or DNS setup
* Ensure server is running

---

## 🏁 Summary

| Device        | Action                                |
| ------------- | ------------------------------------- |
| Server PC     | Install mkcert + generate certificate |
| Other Devices | Install mkcert only                   |
| All Devices   | Use HTTPS URL                         |

---

## 📌 Final Note

This setup is required **only once per device**.

After setup:

* Staff can use the system normally
* No technical steps are needed again
* Camera and secure features will work automatically
