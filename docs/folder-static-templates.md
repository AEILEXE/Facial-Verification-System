# Folder: templates/, static/, staticfiles/, media/

These four folders make up the presentation layer of FANS-C — the HTML pages, CSS styles, JavaScript, images, and user-uploaded files that the browser receives and displays.

---

## templates/ — HTML Page Templates

### Purpose

The `templates/` folder contains all Django HTML templates used to render every web page in the FANS-C system: the login page, dashboard, beneficiary registration form, verification camera interface, logs, and admin pages.

### Why it exists

Django's template engine separates the HTML structure of a page from the Python logic that generates the data for it. Templates receive context data (beneficiary lists, verification results, user roles) from Django views and embed that data into HTML using Django's template language (`{{ variable }}`, `{% block %}`, `{% if %}`).

### Important files inside

Templates are organized into subfolders matching their app:

```
templates/
|-- base.html               Master layout template (navigation, header, notification bell dropdown, footer)
|-- accounts/               Login, logout, user management pages, password-reset OTP flow,
|                            emails/ (branded HTML+plaintext OTP / password-changed emails)
|-- beneficiaries/          Registration form, beneficiary list/detail, duplicate_review queue
|-- verification/           Camera interface, result display, configuration page, stipend events
|-- logs/                   Audit log, verification log, notification center, notification icon partial
|-- fans/                   Dashboard, home page, error pages
```

**base.html** is the master layout. All other templates extend it using Django's template inheritance (`{% extends 'base.html' %}`). This means the navigation bar, header, notification dropdown, and footer are defined once and shared across all pages — this is not an exhaustive subfolder list, just the highest-traffic ones.

### How it connects to the system

- Django views render templates by calling `render(request, 'template_name.html', context_dict)`
- Templates receive context variables (lists, objects, flags) from views and insert them into HTML
- Template inheritance keeps the UI consistent — changing `base.html` changes the layout of every page
- The verification camera page includes JavaScript that calls `navigator.mediaDevices.getUserMedia()` to access the camera and submits frames to the server via AJAX POST requests

### Runtime flow

| Phase | How templates/ is involved |
|---|---|
| Setup | No setup required — templates are static files checked into the repo |
| Runtime | Every page request causes a view to render a template and return HTML |
| Development | Templates can be edited and changes are visible immediately (no restart needed with DEBUG=True) |

### Defense notes

**Why is HTTPS required for the camera page?**
The browser's `getUserMedia()` API only works in secure contexts (HTTPS or localhost). The verification camera template calls this API. Without HTTPS, the camera permission prompt never appears and the camera interface does not work. This is why Caddy (HTTPS termination) is mandatory — not just recommended.

**What happens if a template is missing?**
Django raises a `TemplateDoesNotExist` exception, which results in a 500 Internal Server Error for that page. Other pages are unaffected.

---

## static/ — Source Static Files

### Purpose

The `static/` folder contains the original source versions of all static assets: CSS stylesheets, JavaScript files, fonts, and images used by the web interface.

### Why it exists

Static files are the non-Python, non-HTML assets that make the UI look and behave correctly. Keeping them separate from templates and application code follows Django's convention and makes it easy to find and update styles or scripts without touching application logic.

### Important files inside

```
static/
|-- css/        Stylesheets for all pages
|-- js/         JavaScript files (form validation, camera capture, UI interactions)
|-- images/     Icons, logos, and other images used in the interface
```

### How it connects to the system

- Django templates reference static files using the `{% static 'path/to/file' %}` template tag
- During development (`DEBUG=True`), Django serves static files directly from this folder
- In production, `python manage.py collectstatic` copies everything from `static/` into `staticfiles/` (WhiteNoise serves from there)
- The camera interface JavaScript in `static/js/` handles webcam access, frame capture, and POST submission to the verification endpoint

### Runtime flow

| Phase | How static/ is involved |
|---|---|
| Development | Django serves directly from static/ when DEBUG=True |
| Setup | `collectstatic` copies static/ to staticfiles/ |
| Production | WhiteNoise serves from staticfiles/, not from static/ |

### Defense notes

**Why not just serve from static/ in production?**
Django's development server serves static files, but Waitress does not. In production (Waitress), static files must be handled by a dedicated mechanism. WhiteNoise is a middleware that intercepts requests for static files before they reach Django views and serves them efficiently from `staticfiles/`.

---

## staticfiles/ — Production Static Files

### Purpose

The `staticfiles/` folder is the production-ready collection of all static assets. It is generated by `python manage.py collectstatic` and served by WhiteNoise in production.

### Why it exists

When multiple Django apps each have their own `static/` subfolder, and there are project-level static files too, `collectstatic` merges them all into one folder (`staticfiles/`) with a predictable structure. WhiteNoise then serves files from this single location. This simplifies deployment — no need for a separate Nginx or Apache to serve static files.

### Important files inside

`staticfiles/` contains everything from `static/` plus any static files contributed by installed packages (e.g., Django admin CSS, third-party widgets). It is auto-generated — do not edit files here directly.

### How it connects to the system

- WhiteNoise middleware (configured in `fans/settings.py`) intercepts requests with URLs starting with `/static/` and serves them directly from `staticfiles/`
- This happens inside the Waitress process — no separate file server is needed
- Caddy passes static file requests through to Waitress, which WhiteNoise handles before Django views are called

### Runtime flow

| Phase | How staticfiles/ is involved |
|---|---|
| Setup | Created by `python manage.py collectstatic --noinput` (called during setup) |
| Production runtime | WhiteNoise serves all `/static/` requests from this folder |
| After code changes | Must re-run `collectstatic` to update with any changed static files |

### Defense notes

**What happens if staticfiles/ is missing or empty?**
WhiteNoise cannot serve any static files. The web interface loads without CSS and JavaScript. Pages are functional but visually broken. Fix: run `python manage.py collectstatic --noinput` with the virtual environment activated.

**Why is staticfiles/ in .gitignore?**
It is auto-generated from `static/`. Committing generated files causes merge conflicts and inflates the repository size. The setup script always runs `collectstatic` so the folder is always present after setup.

---

## media/ — User-Uploaded Files

### Purpose

The `media/` folder stores files uploaded by users during application use. In FANS-C, this includes face images uploaded during beneficiary registration.

### Why it exists

Django separates user-uploaded content (media) from developer-provided content (static). Media files are generated at runtime by application logic, not checked into the repository. They grow in size as more beneficiaries are registered and must be managed separately from static assets.

### Important files inside

The contents of `media/` are created at runtime and depend on what has been registered in the system:

```
media/
|-- beneficiaries/      Face images uploaded during registration
|-- [other uploads]     Any other user-uploaded content
```

Face images are stored in `media/` when first uploaded. The extracted face embedding (512-dimensional vector) is then computed by `face_utils.py` and stored encrypted in the database — the original photo in `media/` is used as a reference image for the registration record.

### How it connects to the system

- `fans/settings.py` sets `MEDIA_ROOT = BASE_DIR / 'media'` and `MEDIA_URL = '/media/'`
- When a beneficiary registers, the uploaded photo is saved here by Django's `ImageField`
- In development, Django serves media files directly. In production, Waitress handles `/media/` requests through Django (WhiteNoise is for static files only, not media)
- The Caddyfile forwards all requests — including `/media/` — to Waitress, which handles them via Django

### Runtime flow

| Phase | How media/ is involved |
|---|---|
| Setup | Folder is created automatically by Django on first upload (or by the setup script) |
| Runtime | New face photos are written here during beneficiary registration |
| Backup | media/ must be included in any backup strategy — it contains registration photos |

### Defense notes

**What happens if media/ is deleted?**
The database records for beneficiaries still exist (name, date of birth, stored embedding, etc.), but the registration photos are gone. Face verification can still work because it uses the stored embedding (not the photo), but the visual reference image for the beneficiary record is lost.

**Is media/ backed up?**
It should be. The `media/` folder is not included in the Git repository (it is in `.gitignore`) and must be backed up separately alongside `db.sqlite3` and `.env`.

**Privacy note:**
`media/` contains face photos — personally identifiable biometric data. Access to the folder should be restricted to the server administrator. Do not copy the contents of `media/` to USB drives or share them without proper authorization.

---

## How these folders work together

```
Developer writes CSS/JS → static/
                                |
                         collectstatic
                                |
                                v
                         staticfiles/    ← WhiteNoise serves this in production
                         
Django renders page → templates/        ← HTML with {% static %} references
                                |
                         Browser receives HTML + fetches /static/...
                                |
                                v
                         WhiteNoise intercepts → serves from staticfiles/

User uploads face photo → saved to media/
                                |
                         face_utils.py processes photo → creates embedding
                         embedding stored (encrypted) in db.sqlite3
```

---

## Related folders/files

- `fans/settings.py` — defines STATIC_ROOT, STATICFILES_DIRS, MEDIA_ROOT, MEDIA_URL
- `fans/wsgi.py` — WhiteNoise is added as middleware here or in settings.py
- `requirements.txt` — whitenoise package must be installed
- `manage.py` — `collectstatic` command is run via `python manage.py collectstatic`
- `db.sqlite3` — stores encrypted face embeddings; media/ stores the source photos

---

## Summary

`templates/` defines what the user sees (HTML structure). `static/` is where those assets are written during development. `staticfiles/` is the production-ready copy that WhiteNoise serves efficiently. `media/` holds the runtime content (uploaded photos) that grows as the system is used. All four are essential for the system to function correctly in production.
