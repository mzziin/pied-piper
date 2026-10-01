# Pied Piper — Project Master Context

## 1. Project Identity

**Project Name:** Pied Piper

**Project Title:**  
**Pied Piper: A Secure Peer-to-Peer File Transfer System with Zero-Retention Architecture**

**Project Type:** MCA Academic Project

### One-line description

Pied Piper is a desktop application for secure, reliable peer-to-peer file transfer. The actual file contents should travel directly between sender and receiver whenever possible, without persistent storage of file contents on a central server.

---

## 2. Primary Goal

The primary goal is to build a working, demonstrable P2P file-transfer system with:

- Direct peer-to-peer transfer using WebRTC Data Channels
- Backend-assisted session coordination and signaling
- Zero persistent server-side storage of file contents
- Chunk-based transfer
- Progress and speed reporting
- SHA-256 integrity verification
- Interrupted-transfer recovery and resume
- TURN fallback when direct connectivity is unavailable
- A simple native desktop interface

This is an academic project. The system should be realistic and technically defensible without introducing production-scale complexity that is unnecessary for the project's scope.

---

## 3. Source of Truth and Document Priority

The project has three main specification documents:

1. `PROJECT.md` — project-level implementation context and development rules
2. `SRS` — functional and non-functional requirements
3. `HLD` — architecture and technical design

When implementing:

- Follow the latest approved SRS for requirements.
- Follow the latest approved HLD for architecture.
- Follow this `PROJECT.md` for project conventions, development workflow, scope discipline, and implementation guidance.
- If these documents appear to conflict, **do not silently choose one**. Report the conflict before making a major architectural change.

Research papers, presentations, and other reference documents are supporting material only. They are not specifications.

---

## 4. Technology Stack

### Desktop application

- Python
- PySide6
- Qt Widgets

### Transfer engine

- Python
- aiortc
- WebRTC Data Channels

### Backend

- FastAPI
- WebSocket signaling

### NAT traversal

- STUN
- TURN
- Coturn for TURN fallback

### Persistence

Use the persistence technology defined by the current HLD/implementation plan for transfer metadata and resumable state.

**Never store transferred file contents on the backend.**

### Configuration

- `.env` files for environment-specific configuration
- Never hardcode secrets, API keys, or deployment credentials

---

## 5. Important Desktop Stack Decision

The current desktop implementation is:

**Python + PySide6 + Qt Widgets**

If an older document mentions Electron, React, or TypeScript, do not introduce those technologies into the current desktop implementation.

Do not replace PySide6 with:

- Electron
- React
- TypeScript
- Tauri
- browser-based UI frameworks
- another desktop UI framework

unless the project requirements are formally changed.

---

## 6. Core Architectural Principle

The most important architectural rule is:

> **The server coordinates the peers; the peers transfer the file.**

Conceptually:

```text
                 CONTROL PLANE
        ┌─────────────────────────┐
        │      FastAPI Backend    │
        │                         │
        │  session coordination   │
        │  signaling              │
        │  metadata               │
        └───────────┬─────────────┘
                    │
             WebSocket / API
              ┌─────┴─────┐
              │           │
        Sender Desktop  Receiver Desktop
              │           │
              └─────┬─────┘
                    │
                 DATA PLANE
                    │
              WebRTC DataChannel
                    │
              Actual file data
```

The backend must not become an upload/download storage service.

If direct P2P connectivity fails, WebRTC may use a TURN relay. The relay is a connectivity fallback, not persistent file storage.

---

## 7. Desktop Application Architecture

The PySide6 desktop client should follow this boundary:

```text
PySide6 UI
      ↓
TransferController
      ↓
 ┌───────────────┐
 │               │
 ▼               ▼
BackendClient   TransferEngine
 Interface      Interface
 │               │
 ▼               ▼
FastAPI /       aiortc /
WebSocket       WebRTC
```

During UI development:

```text
BackendClientInterface
        ↓
MockBackendClient

TransferEngineInterface
        ↓
MockTransferEngine
```

The mocks allow the desktop UI to be developed and tested before the real backend and WebRTC engine are ready.

The mocks must remain replaceable.

### UI boundary

PySide6 views/widgets must not directly depend on:

- aiortc internals
- FastAPI
- raw WebSocket implementation
- database implementation
- Coturn
- WebRTC internals
- transfer protocol implementation

UI code communicates through the controller and interfaces.

---

## 8. Core MVP Features

The MVP focuses on:

1. Single-file transfer
2. Sender/receiver transfer-code mechanism
3. WebRTC peer-to-peer transfer
4. WebSocket-based signaling
5. STUN/TURN connectivity
6. Chunked file transfer
7. Transfer progress
8. Speed reporting
9. File integrity verification
10. Interrupted-transfer detection
11. Resume of interrupted transfers
12. Basic error handling
13. Transfer history where required by the approved architecture
14. Native PySide6 desktop interface

### Resume clarification

Resume is **not manual pause**.

If a transfer is interrupted because of a network failure, application interruption, or peer disconnection, the transfer should be able to continue from previously confirmed progress rather than restarting from zero.

Before resuming, the source file must be revalidated according to the approved protocol, including size/hash identity where required.

---

## 9. Transfer-Code Workflow

The MVP uses a transfer-code mechanism.

### Sender

```text
Select file
    ↓
Create transfer session
    ↓
Receive transfer code
    ↓
Share code
    ↓
Wait for receiver
    ↓
Establish WebRTC connection
    ↓
Transfer
    ↓
Verify integrity
    ↓
Complete
```

### Receiver

```text
Enter transfer code
    ↓
Join session
    ↓
Establish connection
    ↓
Review incoming file
    ↓
Accept / Reject
    ↓
Receive file
    ↓
Verify integrity
    ↓
Complete
```

The transfer code is an opaque value from the UI's perspective.

The UI must not depend on implementation details beyond what the interface exposes.

---

## 10. UI Philosophy

The desktop application should be:

- Simple
- Focused
- Professional
- Desktop-native
- Easy to understand
- Keyboard-friendly
- Free from unnecessary dashboard elements

The main workflow should revolve around:

- Send File
- Receive File
- Settings

An active-transfer interface should appear when a transfer is actually active rather than permanently occupying the main screen.

The application is a transfer utility, not a cloud-storage or social platform.

---

## 11. Visual Direction — Authentic Windows 95 Utility

The current UI direction is an **authentic Windows 95-era desktop application aesthetic**.

The goal is:

> A professional file-transfer utility that could plausibly have shipped as a Windows 95 application in the late 1990s.

This should be a genuine native/retro desktop interface, not a modern UI with retro colors.

### Visual principles

Use:

- Classic grey system surfaces
- Navy active title bars
- White title-bar text
- Sharp rectangular controls
- 3D raised and sunken bevels
- Classic Windows-style borders
- Small pixel-style icons
- Compact classic typography
- Traditional menu bars
- Classic status bars
- Rectangular dialogs
- Native-looking file dialogs
- Square buttons
- Recessed list views
- Classic selection highlights
- Keyboard focus rectangles

Avoid:

- Rounded cards
- Pill-shaped buttons
- Large modern hero sections
- Excessive whitespace intended for web dashboards
- Gradients
- Glassmorphism
- Modern floating panels
- Material/Lucide/Font Awesome icon styles
- Emoji as UI icons
- Fake CRT scanlines
- VHS/glitch effects
- Excessive decorative effects

The retro style must remain usable and professional.

---

## 12. Main UI

The primary window should remain simple.

Conceptually:

```text
+------------------------------------------------------+
| Pied Piper                         [_] [□] [X]        |
+------------------------------------------------------+
| File   Transfer   Help                                |
+------------------------------------------------------+
|                                                      |
|                    Pied Piper                         |
|            Secure File Transfer Utility              |
|                                                      |
|        +----------------+  +----------------+        |
|        |                |  |                |        |
|        |   SEND FILE    |  |  RECEIVE FILE  |        |
|        |                |  |                |        |
|        +----------------+  +----------------+        |
|                                                      |
|                      Settings                         |
|                                                      |
+------------------------------------------------------+
| Ready                                      v1.0      |
+------------------------------------------------------+
```

This is a conceptual guide, not a requirement to reproduce the exact arrangement.

---

## 13. Required UI States

The desktop UI should eventually represent the relevant application states:

```text
IDLE
SELECTING_FILE
FILE_SELECTED
CREATING_SESSION
WAITING_FOR_RECEIVER
RECEIVER_CONNECTED
AWAITING_ACCEPTANCE
CONNECTING
TRANSFERRING
INTERRUPTED
RESUMING
COMPLETED
FAILED
CANCELLED
```

Do not implement unnecessary states that have no corresponding application behavior.

State transitions should be controlled by the application/controller layer rather than scattered throughout individual widgets.

---

## 14. Transfer UI Information

When a transfer is active, the UI should be able to display information such as:

- File name
- File size
- Sent/received amount
- Progress percentage
- Progress bar
- Transfer speed
- Estimated time remaining
- Connection status
- P2P/relay status when available
- Current state
- Cancel action

Speed and ETA may initially be placeholders when using the mock engine.

---

## 15. Integrity and Recovery UI

The UI should clearly communicate:

### Successful completion

- Transfer completed
- Integrity verified

### Interrupted transfer

- Transfer interrupted
- Previously confirmed progress retained
- Resume available when valid

### Invalid resume

If the source file no longer matches the original identity:

- Do not silently resume
- Clearly report that resume is unavailable because the source file changed

### Failure

Use concise, technically accurate error messages.

Do not make unsupported security claims such as:

- "Military Grade Encryption"
- "100% Secure"
- "Unhackable"

Prefer factual descriptions such as:

- "Peer-to-peer transfer"
- "Integrity verified"
- "Direct connection"
- "TURN relay"

---

## 16. Zero-Retention Principle

Pied Piper follows a zero-retention principle for file contents.

The backend may maintain information required for:

- Session coordination
- Signaling
- Transfer metadata
- Connection state
- Transfer history where required

The backend must not persist the actual transferred file contents.

Do not implement an architecture where file bytes pass through a FastAPI upload endpoint or are stored in a database as a staging mechanism.

---

## 17. Security Principles

Use established mechanisms instead of unnecessary custom cryptography.

Security-relevant areas include:

- Secure signaling
- Session/transfer-code validation
- Authentication where required by the approved SRS
- WebRTC transport security
- SHA-256 integrity verification
- Secure configuration and secrets
- Safe temporary-file handling
- Proper authorization of session operations

Do not invent a custom encryption or transport protocol.

Application-level encryption is not an automatic MVP requirement unless explicitly approved as part of the current requirements.

---

## 18. Reliability Principles

Reliability is more important than optional features.

The system should prioritize:

1. Correct connection establishment
2. Correct file transfer
3. Correct chunk handling
4. Correct integrity verification
5. Correct interruption handling
6. Correct resume behavior
7. Correct cleanup
8. TURN fallback
9. UI polish
10. Optional features

Do not sacrifice reliable transfer behavior merely to add visual or convenience features.

---

## 19. Scope Exclusions

Unless the approved requirements are changed, do not add:

- Chat
- Contacts
- Social features
- Cloud storage
- Permanent file hosting
- Folder transfer
- Multi-device synchronization
- Voice/video calling
- Blockchain
- Browser client
- Multiple simultaneous transfers
- Manual pause as a core feature
- Unnecessary user-profile systems
- Custom cryptography
- Custom networking protocols
- Electron
- React
- TypeScript
- Tauri
- Unnecessary third-party UI frameworks

Features outside the MVP belong in future scope rather than being implemented automatically.

---

## 20. Simplicity Rule

Every technology, abstraction, dependency, and design pattern must justify its existence.

Prefer:

- Simple Python classes
- Simple PySide6 widgets
- Qt signals/slots
- Small controllers
- Clear interfaces
- Straightforward data models
- Standard library functionality where practical

Avoid introducing:

- Factories without a real need
- Repositories without a real need
- Managers that merely wrap another class
- Providers
- Facades
- Strategy hierarchies
- Dependency-injection frameworks
- Complex state-management frameworks
- Excessive adapter layers
- Premature asynchronous abstractions

**Do not make the code complicated just to achieve a sophisticated appearance.**

---

## 21. Incremental Development Rule

The project must be developed in small, independently verifiable steps.

**Do not implement the entire application in one prompt or one commit.**

Each implementation step should:

1. Have one clear objective.
2. Modify only the files necessary for that objective.
3. Avoid unrelated refactoring.
4. Be independently testable.
5. Leave the repository in a working state.
6. Be suitable for its own Git commit.

After completing a step, the coding agent must:

- Explain what changed.
- List created/modified files.
- Explain how to test it.
- Report any problems.
- Stop.

The agent must **not automatically continue to the next step**.

The next step begins only when explicitly instructed.

---

## 22. Git Commit Discipline

Each completed implementation step should normally correspond to one focused Git commit.

Example:

```text
feat: initialize desktop application
feat: add application controller boundary
feat: add windows 95 base theme
feat: add main window
feat: add send workflow
feat: add receive workflow
feat: add transfer state UI
feat: add transfer progress
feat: add recovery states
feat: add settings dialog
```

Do not mix unrelated changes into a commit.

Do not rewrite Git history unless explicitly instructed.

---

## 23. Testing Rule

Every step must have a clear verification method.

For UI work, at minimum verify:

- Application starts
- Relevant screen appears
- Buttons work
- Navigation/state changes work
- No unrelated workflow broke
- No obvious console/runtime errors

For backend/transfer work, use the appropriate automated or integration tests defined by the project.

The goal is not exhaustive testing of every line. Focus testing effort on high-risk behavior.

---

## 24. Mock Implementations

The UI prototype should initially use:

```text
MockBackendClient
MockTransferEngine
```

The mock transfer engine should be capable of simulating enough behavior to demonstrate UI state transitions, including:

- Waiting for receiver
- Receiver connection
- Transfer start
- Progress updates
- Completion
- Interruption
- Resume
- Failure

The mock must not pretend to be the real WebRTC implementation.

The real aiortc engine should be replaceable without redesigning the UI.

---

## 25. Async and Threading

The UI must remain responsive.

Do not perform blocking network or file-transfer operations directly on the Qt UI thread.

However, do not introduce complicated async/threading infrastructure prematurely.

When real async integration is required, use the smallest architecture that preserves:

- Responsive UI
- Clear ownership
- Clean shutdown
- Predictable event delivery

---

## 26. Repository Structure

The project uses a single repository with separate desktop and backend areas.

A reasonable target structure is:

```text
pied-piper/
├── PROJECT.md
├── README.md
├── .env.example
├── .gitignore
│
├── desktop/
│   ├── app/
│   │   ├── main.py
│   │   ├── ui/
│   │   ├── controllers/
│   │   ├── models/
│   │   ├── services/
│   │   └── resources/
│   ├── tests/
│   └── requirements.txt
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── services/
│   │   └── models/
│   ├── tests/
│   └── requirements.txt
│
└── docs/
```

This structure is a guide, not permission to create unnecessary subpackages.

If a directory is not needed yet, do not create it merely because it appears in the example.

---

## 27. Development Order

The implementation should progress from a minimal foundation toward the real system.

### Foundation

1. Repository and project configuration
2. Minimal desktop application launch
3. Minimal backend launch
4. Basic interfaces and boundaries

### Connectivity

5. Signaling/rendezvous
6. WebRTC connection
7. DataChannel establishment

### Transfer

8. Basic single-file transfer
9. Chunking
10. Flow control
11. Progress
12. Integrity verification

### Reliability

13. Checkpointing
14. Persistent transfer state
15. Reconnection
16. Resume
17. Recovery and cleanup

### Networking

18. Real-network validation
19. TURN fallback

### Desktop integration

20. Connect the PySide6 UI to the real interfaces
21. Transfer history/settings as required
22. Final UI polish

### Hardening and release

23. Security hardening
24. Performance testing
25. Packaging
26. Documentation
27. Final demonstration preparation

The exact order may be adjusted by the current implementation plan, but major architectural decisions should not be changed casually.

---

## 28. Current Desktop UI Development Strategy

The desktop UI should initially be developed independently from the real transfer engine.

Use:

```text
PySide6
   ↓
TransferController
   ↓
MockBackendClient
MockTransferEngine
```

The objective is to prove:

- UI structure
- State transitions
- User flow
- Controller boundaries
- Error handling
- Recovery presentation

before depending on the real network stack.

After the UI foundation is stable, connect the actual backend and aiortc implementation through the same interfaces.

---

## 29. AI Coding-Agent Rules

When an AI coding agent is used on this project:

1. Read `PROJECT.md` before implementing a new feature.
2. Read the relevant SRS/HLD sections when requirements or architecture are involved.
3. Work only on the explicitly requested step.
4. Do not implement future steps automatically.
5. Do not modify unrelated files.
6. Do not perform broad refactoring unless explicitly requested.
7. Preserve existing functionality.
8. Prefer modifying existing simple code over introducing abstractions.
9. Do not introduce a dependency without explaining why it is necessary.
10. Do not change the desktop technology stack.
11. Keep UI, controller, backend, and transfer-engine responsibilities separate.
12. Never place file-transfer/networking logic directly inside UI widgets.
13. Never store transferred file contents on the backend.
14. Do not silently resolve conflicts between project documents.
15. If an architectural change appears necessary, stop and explain the trade-off before implementing it.
16. After each step, provide a concise change summary and testing instructions.
17. Stop after the requested step and wait for the next instruction.

---

## 30. Definition of a Good Change

A good change is:

- Small
- Understandable
- Testable
- Reversible
- Focused
- Compatible with the existing architecture
- Easy to commit
- Easy for another developer to review

A change is not good merely because it is technically sophisticated.

---

## 31. Final Project Principle

The project should always optimize for:

> **The simplest working version of Pied Piper that proves secure, direct, resumable peer-to-peer file transfer with no persistent server-side storage of file contents.**

Build the core correctly.

Keep the architecture understandable.

Add complexity only when the requirements genuinely require it.

Build progressively.

Test every step.

Commit every meaningful step.

Do not expand the scope without an explicit project decision.