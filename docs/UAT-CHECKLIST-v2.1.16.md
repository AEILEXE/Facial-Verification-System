# FANS-C v2.1.16 — User Acceptance Testing (UAT) Checklist

**For: non-developer testers** (barangay staff, admin, officers). No coding or technical knowledge is needed to use this document — just follow each step in order and record what actually happens.

## How to use this checklist

- Work through the sections **in order** — later sections assume earlier ones (e.g. Login) already work.
- For every numbered item: do the **Steps**, compare what happens to the **Expected Result**, then tick **Pass** or **Fail**.
- If something doesn't match the Expected Result, don't try to fix it — write it up using the **Bug/Issue Report Format** at the end of this document (Section 11) and keep testing the rest of the checklist.
- Test on the actual installed application (the one you open via the desktop/Start Menu shortcut), not on a developer's computer running special developer tools.
- Fill in the box below before you start.

```
Tester name:        _______________________________
Date tested:        _______________________________
Computer used:      _______________________________
Installer file:     FANS-C-Setup-v2.1.16.exe
Install/EXE version shown in app (see Section 3): _______________________________
```

---

## Section 1 — Installation Test

### 1.1 Run the installer
- **Steps:** Double-click `FANS-C-Setup-v2.1.16.exe`. If Windows asks "Do you want to allow this app to make changes to your device?", click **Yes**.
- **Expected result:** A setup wizard window opens showing "FANS-C Verification System."
- [ ] Pass  [ ] Fail

### 1.2 Choose install location
- **Steps:** Follow the wizard. When asked where to install, the default should already be filled in.
- **Expected result:** Default install folder is `C:\FANSC` (do not need to type anything unless your organization asked you to change it).
- [ ] Pass  [ ] Fail

### 1.3 Complete installation
- **Steps:** Continue clicking through the wizard (Next → Install → Finish). Leave "Launch FANS-C now" checked on the last screen.
- **Expected result:** Installation completes without any red error screens. A "Desktop shortcut" and a Start Menu entry named "FANS-C" appear.
- [ ] Pass  [ ] Fail

### 1.4 Shortcuts work
- **Steps:** Look for a "FANS-C Verification System" icon on the Desktop and in the Start Menu.
- **Expected result:** Both shortcuts exist and have the FANS-C logo icon (not a generic blank icon).
- [ ] Pass  [ ] Fail

---

## Section 2 — First-Run Setup

*This only happens once, the very first time the application is opened after installing.*

### 2.1 Setup wizard appears
- **Steps:** After installation, the app should open on its own (or double-click the Desktop shortcut).
- **Expected result:** A small setup window appears showing numbered steps in progress (generating security keys, preparing the database, preparing the web address, etc.) — this can take a few minutes. Don't close the window.
- [ ] Pass  [ ] Fail

### 2.2 Create the first account (Technical Administrator)
- **Steps:** When prompted, fill in a username, full name, email (optional), and a password for the very first account.
- **Expected result:** The form requires a reasonably strong password (the app will tell you if it's too weak) and creates the account without errors. This first account is always the **Technical Administrator** role — this is expected and correct.
- [ ] Pass  [ ] Fail

### 2.3 Setup finishes and the app opens in your browser
- **Steps:** Wait for the wizard to say setup is complete.
- **Expected result:** Your default web browser opens automatically to a page starting with `https://fans-barangay.local`. You should NOT see a scary red "Not Secure" / "Your connection is not private" warning page. A small padlock icon should appear next to the address.
- [ ] Pass  [ ] Fail

### 2.4 Confirm version number
- **Steps:** Once logged in (see Section 3), find the version number — check the bottom of the page or an "About"/System Info area if present.
- **Expected result:** Version shown matches **2.1.16**. Write this down at the top of this document.
- [ ] Pass  [ ] Fail

---

## Section 3 — Login

### 3.1 Log in with the account you just created
- **Steps:** Enter the username/password from Section 2.2 on the login page.
- **Expected result:** Login succeeds and a dashboard page appears.
- [ ] Pass  [ ] Fail

### 3.2 Wrong password is rejected
- **Steps:** Log out, then try logging in with the correct username but a wrong password.
- **Expected result:** A clear "invalid username or password" style message appears — the app does not log you in and does not reveal whether the username itself was correct or not.
- [ ] Pass  [ ] Fail

### 3.3 Repeated wrong passwords lock the account temporarily
- **Steps:** Try the wrong password several times in a row (about 8 attempts) within a few minutes.
- **Expected result:** After several failed attempts, the app tells you the account is temporarily locked rather than letting you keep guessing.
- [ ] Pass  [ ] Fail

### 3.4 Log out works
- **Steps:** Use the Log Out option (usually top-right menu).
- **Expected result:** You're returned to the login page, and going "Back" in the browser does not show any private page content.
- [ ] Pass  [ ] Fail

---

## Section 4 — Role Permissions

You will need accounts of each role for this section. Ask your Technical Administrator to create test accounts for: **Staff**, **Admin**, **President**, and use the **Technical Administrator** account from Section 2.

### 4.1 Staff — sees only front-line menus
- **Steps:** Log in as Staff. Look at the top menu bar.
- **Expected result:** You see Dashboard, Beneficiaries, Verify, and Audit & Logs (Verification Logs only). You do **not** see Distribution, Analytics, or Administration menus.
- [ ] Pass  [ ] Fail

### 4.2 Admin — sees operations + analytics, but not technical tools
- **Steps:** Log in as Admin. Check the top menu bar, especially any Analytics section.
- **Expected result:** Admin sees Dashboard, Beneficiaries, Verify, Distribution, Audit & Logs, Analytics, and Administration. Inside Analytics, Admin sees Executive, Operational, and Security tabs — but **no "System Evaluation & Research" tab**, and no "Technical Administration" menu.
- [ ] Pass  [ ] Fail

### 4.3 Admin cannot reach System Evaluation & Research even by typing the address
- **Steps:** While logged in as Admin, if you know the technical web address for the evaluation/research page, try typing it directly into the browser address bar instead of clicking a menu. (Skip this test if you don't have that address — ask your Technical Administrator.)
- **Expected result:** Admin is redirected away with a message saying that page is restricted to Technical Administrator/President — the page never actually opens for Admin.
- [ ] Pass  [ ] Fail

### 4.4 Technical Administrator — full technical access
- **Steps:** Log in as Technical Administrator. Check Analytics and Administration menus.
- **Expected result:** Technical Administrator sees everything Admin sees, **plus** a "System Evaluation & Research" tab under Analytics and a "Technical Administration" menu (Evaluation Sessions, Backup & Database Status, Network & HTTPS Status).
- [ ] Pass  [ ] Fail

### 4.5 President — same as Admin, plus read-only research access
- **Steps:** Log in as President. Check Analytics.
- **Expected result:** President sees the same as Admin, plus a "System Evaluation & Research" tab (like Technical Administrator) — but any "create" or "edit" buttons on that page should be missing or disabled for President (read-only).
- [ ] Pass  [ ] Fail

### 4.6 Payout approval is restricted correctly
- **Steps:** As Technical Administrator, try to release or override a stipend payout (see Section 7). As Admin or President, do the same.
- **Expected result:** Technical Administrator should **not** be able to approve/release/override a payout, even though they can see everything else — that action should be blocked or hidden for them. President and/or Admin should be able to (per your organization's usual sign-off rule).
- [ ] Pass  [ ] Fail

### 4.7 Only President can create a Technical Administrator account
- **Steps:** As Admin, go to User Management and try to create a new account with the Technical Administrator role.
- **Expected result:** The option to assign the Technical Administrator role is not available to Admin — only President can create that role.
- [ ] Pass  [ ] Fail

---

## Section 5 — Beneficiary Workflow

### 5.1 Register a new beneficiary
- **Steps:** Go to Beneficiaries → Register. Fill in name, date of birth, address/barangay, government ID info, and capture/register a face photo per the on-screen instructions.
- **Expected result:** Registration completes and the new beneficiary appears in the beneficiary list.
- [ ] Pass  [ ] Fail

### 5.2 Search for a beneficiary
- **Steps:** Use the search box on the Beneficiaries list to search by name and separately by Senior Citizen ID.
- **Expected result:** Both searches correctly find the beneficiary you just registered.
- [ ] Pass  [ ] Fail

### 5.3 View beneficiary profile
- **Steps:** Open the beneficiary's detail page.
- **Expected result:** The page is organized into clear sections — Personal Information, Address, Government ID, Verification History, Claim History, Authorized Representatives — not one long jumbled list.
- [ ] Pass  [ ] Fail

### 5.4 Edit a beneficiary
- **Steps:** Edit the beneficiary's contact number or address and save.
- **Expected result:** Change saves successfully and shows on the profile immediately.
- [ ] Pass  [ ] Fail

### 5.5 Duplicate-face detection
- **Steps:** Ask your Technical Administrator to help you try registering the same person's face a second time under a different name (test data only, not a real second beneficiary).
- **Expected result:** The system flags this as a possible duplicate rather than silently allowing two active records for the same face.
- [ ] Pass  [ ] Fail

---

## Section 6 — Verification Workflow

### 6.1 Basic verification (camera capture)
- **Steps:** Go to Verify, search for the beneficiary, and follow the on-screen camera capture instructions to verify their identity.
- **Expected result:** The camera preview appears in the browser, capture completes, and the system shows a clear result (Verified / Manual Review / Denied).
- [ ] Pass  [ ] Fail

### 6.2 Liveness / anti-spoofing check
- **Steps:** During capture, follow any on-screen instruction (e.g. "turn your head" / "look left/right").
- **Expected result:** The instruction is clear and the system responds to real head movement. Holding up a photo or a phone screen to the camera instead of a real face should be caught and rejected/flagged, not silently accepted.
- [ ] Pass  [ ] Fail

### 6.3 Manual Review queue
- **Steps:** If a verification attempt results in "Manual Review," check the Manual Review queue as Admin/President.
- **Expected result:** The attempt appears in the queue with enough detail to make a decision, and an authorized reviewer can approve or deny it.
- [ ] Pass  [ ] Fail

### 6.4 Denied verification is handled clearly
- **Steps:** Attempt verification for someone who is not the registered beneficiary (a different tester, as a test), if your test plan allows this.
- **Expected result:** The system denies/does not verify, with a message that doesn't confuse the operator, and does not silently allow the claim to proceed.
- [ ] Pass  [ ] Fail

---

## Section 7 — Payout Workflow

### 7.1 Claim a stipend after successful verification
- **Steps:** After a successful verification tied to an active stipend event, complete the claim/payout release steps.
- **Expected result:** The payout is recorded, the beneficiary's claim history updates, and a reference number is shown.
- [ ] Pass  [ ] Fail

### 7.2 Duplicate payout is blocked
- **Steps:** Try to claim the same stipend event a second time for the same beneficiary on the same day.
- **Expected result:** The system blocks the second claim and explains why (already claimed).
- [ ] Pass  [ ] Fail

### 7.3 Manual override / fallback payout (Admin/President only)
- **Steps:** As Admin or President, use the manual override option for a claim that needs a documented exception (e.g. verification hardware issue).
- **Expected result:** An override reason is required before the payout can be released, and it's recorded against the approving user's name.
- [ ] Pass  [ ] Fail

### 7.4 Pending claims are visible
- **Steps:** Check the Analytics → Executive tab for a "Pending Claims" figure.
- **Expected result:** The number shown matches the number of claims actually awaiting approval.
- [ ] Pass  [ ] Fail

---

## Section 8 — Reports

### 8.1 Beneficiary Master List report
- **Steps:** Open Reports → Beneficiary Master List and apply a filter (e.g. by barangay or status).
- **Expected result:** The list on screen matches the filter, with readable column headers (not raw computer field names).
- [ ] Pass  [ ] Fail

### 8.2 Distribution / Event Summary report
- **Steps:** Open the Distribution/Event Summary report for a specific stipend event.
- **Expected result:** Totals shown (claimed, pending, rejected, attempts) look correct against what you know about that event.
- [ ] Pass  [ ] Fail

### 8.3 Analytics dashboards
- **Steps:** Open Analytics → Executive, then Operational, then Security tabs.
- **Expected result:** Each tab loads with numbers/charts that make sense for a stipend distribution system (beneficiary counts, verification volumes, distribution by barangay, security alerts) — nothing looks like a blank/broken page.
- [ ] Pass  [ ] Fail

### 8.4 Audit Log search
- **Steps:** As Admin/President/Technical Administrator, open Audit Logs. Try filtering by a date range, a specific username, a Role, and an Action Type together.
- **Expected result:** All the filters can be combined at once and the results narrow down correctly (e.g. "everything Staff A did in a given month").
- [ ] Pass  [ ] Fail

---

## Section 9 — Exports

### 9.1 CSV export
- **Steps:** From a report screen (e.g. Beneficiary Master List or Claims), export to CSV and open it in Excel or a spreadsheet program.
- **Expected result:** Column headers are readable words (not computer field names like `beneficiary_id`), dates are consistent, and any peso amounts are formatted clearly (not a raw unformatted number).
- [ ] Pass  [ ] Fail

### 9.2 Excel export
- **Steps:** Export the same report to Excel format and open it.
- **Expected result:** The file opens with a title, generated-by/date line, bold column headers, and readable column widths (no `####` symbols from too-narrow columns). Peso amounts show with commas.
- [ ] Pass  [ ] Fail

### 9.3 PDF export
- **Steps:** Export a report that offers a PDF option (e.g. Beneficiary Master List or a beneficiary's payout history).
- **Expected result:** The PDF looks like a professional government report — title, generated-by/date, a page number in the footer, and a readable table (not cramped or cut off).
- [ ] Pass  [ ] Fail

---

## Section 10 — Security Observations

*You don't need technical knowledge for this section — just note anything that feels wrong or surprising.*

### 10.1 HTTPS / secure connection
- **Steps:** Look at the browser address bar while using the app.
- **Expected result:** The address starts with `https://` and shows a padlock icon, with no browser security warnings.
- [ ] Pass  [ ] Fail

### 10.2 Session timeout
- **Steps:** Log in, then leave the app idle (no clicks) for a while per your organization's policy, then try to do something.
- **Expected result:** You're asked to log in again after being idle for too long, rather than staying logged in forever.
- [ ] Pass  [ ] Fail

### 10.3 No sensitive data visible to the wrong role
- **Steps:** While logged in as Staff, try to find any screen that shows other users' personal login info, raw biometric scores, or system configuration.
- **Expected result:** Staff never sees this information anywhere in the app.
- [ ] Pass  [ ] Fail

### 10.4 Password requirements
- **Steps:** When creating or changing a password, try a weak one like `123456`.
- **Expected result:** The app rejects it and explains what's required.
- [ ] Pass  [ ] Fail

### 10.5 General observations
- **Steps:** Note anything else that felt insecure, confusing, or "off" while testing — even if you're not sure it's actually a problem.
- **Notes:** _______________________________________________

---

## Section 11 — Bug / Issue Report Format

For **every** item marked **Fail** above, or anything else you notice, copy this block and fill it in — one per issue:

```
BUG / ISSUE REPORT
-------------------
Title (one line summary):

Section/Item number (e.g. 6.2):

Role/account used (Staff / Admin / President / Technical Administrator):

Steps to reproduce (exactly what you clicked, in order):
1.
2.
3.

What you expected to happen:

What actually happened:

Screenshot attached? (Yes/No):

How severe does this feel to you?
[ ] Blocks testing entirely (can't continue)
[ ] Major (wrong result / wrong permission / data issue)
[ ] Minor (confusing wording, small visual issue)
[ ] Cosmetic only

Date/time observed:

Tester name:
```

Save each filled-in report and send them together with this completed checklist back to the development team.

---

## Sign-off

```
Total items tested:      _____
Total Pass:               _____
Total Fail:                _____
Tester signature:         _______________________________
Date completed:            _______________________________
```
