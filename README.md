# FANSC
## Secure FaceNet-Based Facial Verification System for Senior Citizen Stipend Distribution

A secure biometric identity verification platform designed to improve the accuracy, security, and efficiency of senior citizen stipend distribution through facial recognition, liveness detection, presentation attack detection, and audit-controlled workflows.

---

# 📌 Overview

Traditional stipend distribution processes often rely on manual identity verification and paper-based records. These approaches may introduce delays, duplicate records, identity verification risks, and limited transparency during distribution activities.

FANSC addresses these challenges by providing a secure digital verification platform that combines:

- Deep learning-based facial recognition
- Server-side liveness verification
- Presentation Attack Detection (PAD)
- Secure beneficiary management
- Role-based access control
- Audit logging
- Distribution monitoring and reporting

The system is designed for controlled local government deployment environments where secure beneficiary verification and transparent stipend distribution are required.

---

# ✨ Key Features

## 🔐 Face Recognition Verification

FANSC uses FaceNet-based facial verification to confirm beneficiary identity.

The verification workflow includes:

1. Live face capture
2. Face detection
3. Liveness validation
4. Presentation attack analysis
5. Facial embedding comparison
6. Verification decision recording

The system does not rely on client-side verification results. Security decisions are performed server-side.

---

## 🛡️ Liveness Detection and Anti-Spoofing

FANSC includes multiple security layers to prevent presentation attacks.

Protection against:

- Printed photographs
- Mobile phone screen replay
- Static image attacks
- Recorded video attempts
- Fake verification submissions

Security mechanisms include:

- Server-side facial movement analysis
- Head movement challenge verification
- Texture-based anti-spoofing checks
- Replay evidence detection
- Presentation Attack Detection (PAD)

---

## 👥 Role-Based Access Control

FANSC implements controlled user access through system roles.

| Role | Description |
|---|---|
| President | Operational oversight and authorized access |
| Administrator | Beneficiary, verification, reports, and management functions |
| Technical Administrator | System configuration and technical maintenance |
| Staff | Registration and verification operations |

---

## 👴 Beneficiary Management

The system manages senior citizen beneficiary records including:

- Registration
- Profile management
- Verification status
- Duplicate face detection
- Beneficiary history tracking
- Status monitoring

---

## 💰 Stipend Distribution Management

FANSC supports stipend distribution workflows:

- Distribution event management
- Beneficiary verification before claiming
- Claim monitoring
- Payout recording
- Distribution reporting

---

## 📊 Analytics and Monitoring Dashboard

The system provides operational insights through:

- Executive analytics
- Verification statistics
- Distribution progress monitoring
- Security indicators
- Audit activity tracking
- Performance reports

---

## 📝 Audit Logging

All important system actions are recorded.

Examples:

- Login attempts
- Verification attempts
- Beneficiary changes
- Administrative actions
- Password operations
- Manual review activities
- Security-related events

Audit records improve transparency and accountability.

---

# 🏗️ System Architecture

```
Client Devices
      |
      | HTTPS
      |
Caddy Reverse Proxy
      |
      |
Waitress Application Server
      |
      |
Django Framework
      |
      |
Database + Face Recognition Models
```

Architecture characteristics:

- Local network deployment
- Secure HTTPS communication
- Centralized verification server
- No cloud dependency
- Controlled biometric processing environment

---

# 🧰 Technology Stack

| Component | Technology |
|---|---|
| Backend Framework | Django 4.2 |
| Programming Language | Python 3.11 |
| Web Server | Waitress |
| HTTPS Proxy | Caddy |
| Face Recognition | FaceNet |
| Face Detection | MTCNN |
| Frontend | HTML, CSS, JavaScript, Bootstrap |
| Database | SQLite / PostgreSQL |
| Deployment | Windows Installer Package |

---

# 🔒 Security Design

## Data Protection

FANSC applies security practices including:

- Environment-based configuration
- Protected secrets management
- Encrypted biometric embeddings
- Restricted media access
- Role-based permissions

---

## Verification Security

The system follows a server-authoritative security model.

Client devices cannot directly authorize:

- Liveness success
- Identity verification
- Anti-spoofing decisions

All critical decisions are validated by the server.

---

# 🚀 Deployment

FANSC supports controlled deployment through an installer-based setup.

The deployment package includes:

- Application runtime
- Required dependencies
- Server configuration
- HTTPS setup
- Database initialization
- System configuration tools

For detailed instructions, see:

- `SETUP.md`
- `DEPLOYMENT.md`
- `CLIENT_ACCESS.md`

---

# 📂 Project Structure

```
FANSC/
│
├── accounts/              # Authentication and user management
├── beneficiaries/         # Beneficiary records and workflows
├── verification/          # Facial verification and liveness system
├── logs/                  # Audit logging
├── fans/                  # Main Django configuration
│
├── templates/             # HTML templates
├── static/                # CSS and JavaScript assets
├── docs/                  # Documentation
├── dev/                   # Development and build tools
│
├── manage.py
├── requirements.txt
└── README.md
```

---

# 🧪 Testing and Validation

FANSC includes:

- Automated Django testing
- Security regression testing
- Deployment validation
- Migration verification
- Template and system checks

Validation covers:

- Authentication workflows
- Password recovery
- Beneficiary management
- Face verification
- Liveness detection
- Distribution workflows
- Analytics and reporting

---

# 📚 Documentation

Additional documentation:

| Document | Purpose |
|---|---|
| [docs/FANSC-SYSTEM-REFERENCE.md](docs/FANSC-SYSTEM-REFERENCE.md) | **Complete technical system reference** — architecture, data model, biometric pipeline, security, deployment, and more, in one document |
| SETUP.md | Installation and environment setup |
| DEPLOYMENT.md | Deployment procedures |
| CLIENT_ACCESS.md | Client workstation configuration |
| SECURITY.md | Security architecture |
| PRIVACY.md | Data protection considerations |
| SYSTEM-OVERVIEW.md | System architecture overview |
| DATABASE-GUIDE.md | Database reference |
| MAINTENANCE-PLAN.md | Maintenance procedures |

---

# 🎯 Research Contribution

FANSC demonstrates the application of biometric verification technology for improving public service distribution.

The system combines:

- Deep learning facial recognition
- Anti-spoofing mechanisms
- Secure software engineering practices
- Audit-based accountability
- Role-controlled operational workflows

The project focuses on practical deployment considerations including:

- Data privacy
- Local government workflows
- Security requirements
- Operational reliability

---

# 🔮 Future Improvements

Possible future enhancements include:

- Advanced anti-spoofing models
- Expanded analytics capabilities
- Multi-location deployment support
- Additional biometric security options
- Improved large-scale deployment management

---

# 📄 License

This project is developed for academic research and controlled deployment purposes.

---

# 📌 Release Information

Current Version:

```
FANSC v2.1.17 Official Release
```

Status:

```
Production-ready deployment package
```
