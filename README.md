# Pied Piper 🚀

A peer-to-peer (P2P) file transfer engine built on WebRTC DataChannels with an ephemeral WebSocket signaling service, receiver-authoritative protocol, whole-file and per-chunk SHA-256 integrity verification, and SQLite transfer state persistence.

---

## Architecture Overview

```
Desktop App (Human Developer)
    │  (consumes async event callbacks)
    ▼
Transfer API (`backend/api/transfer_api.py`)
    │
    ▼
Transfer Orchestration (`backend/transfer/`)
    │
    ▼
Protocol Layer (`backend/protocol/`)
    │
    ▼
Transport Layer (`backend/transport/`)  [aiortc RTCPeerConnection: control + data DataChannels]
    │
    ▼
Signaling Client (`backend/signaling/`) [WebSocket room management, SDP/ICE relay]
```

For complete technical specifications, see [PROJECT.md](PROJECT.md) and [docs/milestone_1_lan.md](docs/milestone_1_lan.md).

---

## Repository Structure

```
pied-piper/
├── README.md
├── PROJECT.md
├── LICENSE                       # MIT
├── .env.example
├── .gitignore
├── requirements.txt
├── docs/
│   └── milestone_1_lan.md        # Phases 1–6 specifications
├── backend/
│   ├── __init__.py
│   ├── config.py                 # Typed settings loader with .env support
│   ├── signaling/                # FastAPI signaling server & room management
│   │   ├── __init__.py
│   │   ├── server.py             # FastAPI app & standalone CLI runner
│   │   ├── rooms.py              # Ephemeral room model, code generator, TTL sweeper
│   │   └── messages.py           # Signaling protocol schemas
│   ├── transport/                # aiortc WebRTC peer connection & DataChannels
│   │   ├── __init__.py
│   │   ├── peer_connection.py    # RTCPeerConnection wrapper & SDP/ICE handshake
│   │   └── data_channels.py      # Control & data DataChannel manager
│   ├── protocol/                 # Wire framing, chunking, hashing, window logic
│   │   ├── __init__.py
│   │   ├── framing.py            # Message schemas & binary chunk frames
│   │   ├── chunking.py           # Chunk reader, SHA-256 hashing, filename sanitizer
│   │   └── window.py             # Sliding window flow control (Phase 8+)
│   ├── transfer/                 # Transfer orchestration, sender/receiver, SQLite store
│   │   ├── __init__.py
│   │   ├── session.py            # Transfer session lifecycle
│   │   ├── state_store.py        # SQLite persistence (Phase 10+)
│   │   ├── sender.py             # Stop-and-wait binary file sender
│   │   └── receiver.py           # Verified atomic file receiver
│   ├── api/                      # Async Transfer API for desktop frontend (Phase 17+)
│   │   ├── __init__.py
│   │   └── transfer_api.py
│   └── cli/                      # Reference peer CLI client
│       ├── __init__.py
│       └── peer.py               # Complete CLI sender & receiver client
├── desktop/                      # Desktop UI integration boundary (Phase 17+)
│   └── README.md
└── tests/                        # Automated test suite
    ├── __init__.py
    ├── test_cli.py
    ├── test_config.py
    ├── test_signaling.py
    ├── test_transport.py
    ├── test_protocol.py
    └── test_transfer.py
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- Standard `venv` and `pip`

### 1. Virtual Environment Setup

Create and activate a virtual environment:

```bash
# Create virtual environment
python -m venv env

# Activate virtual environment
# On Linux / macOS:
source env/bin/activate

# On Windows (Command Prompt / PowerShell):
# env\Scripts\activate.bat   (cmd)
# .\env\Scripts\Activate.ps1  (PowerShell)
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment Configuration

Copy the sample environment configuration:

```bash
cp .env.example .env
```

Key configuration parameters (`backend/config.py`):
| Key | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | Environment mode |
| `SIGNALING_HOST` | `0.0.0.0` | Host to bind signaling server |
| `SIGNALING_PORT` | `8000` | Port to bind signaling server |
| `SIGNALING_URL` | `ws://localhost:8000/ws` | WebSocket endpoint for signaling |
| `STUN_URLS` | `stun:stun.l.google.com:19302` | STUN server URLs for NAT traversal |
| `CHUNK_SIZE_BYTES`| `16384` | Transfer chunk size (16 KB default) |
| `SLIDING_WINDOW_SIZE` | `32` | Sliding window capacity |
| `SQLITE_PATH` | `./pied_piper.db` | Local SQLite database path |
| `ROOM_TTL_SECONDS`| `900` | Ephemeral room TTL (15 minutes) |

---

## Running the Signaling Server

Start the standalone WebSocket signaling service:

```bash
python -m backend.signaling.server --host 0.0.0.0 --port 8000
```

---

## Running the Desktop Application

The signaling server must be running as a standalone service before launching the desktop client:

1. **Start the standalone signaling server** (on the host machine):
   ```bash
   python -m backend.signaling.server --host 0.0.0.0 --port 8000
   ```

2. **Launch the Desktop Application**:
   - **Sender**:
     ```bash
     python desktop/main.py
     ```
     Click **Send File...**, choose a file, and click **Send**. The sender creates a room on the signaling server and displays the 6-character room code.
   - **Receiver**:
     - On the same machine:
       ```bash
       python desktop/main.py
       ```
     - On another machine on the same LAN:
       ```bash
       SIGNALING_URL=ws://<HOST_LAN_IP>:8000/ws python desktop/main.py
       ```
     Click **Receive File...**, enter the 6-character room code, and click **Continue**. Both peers negotiate direct WebRTC DataChannels and stream the file.

---

## Running Single-Machine Transfers (CLI Reference Peer)

### Sender
```bash
python -m backend.cli.peer --role send --file path/to/sample.bin
```
The CLI will generate and print a 6-character room code (e.g. `GFJARX`) and wait for the receiver.

### Receiver
```bash
python -m backend.cli.peer --role receive --room-code GFJARX --output-dir ./downloads
```
Both peers negotiate WebRTC, open DataChannels, stream chunks with per-chunk SHA-256 integrity verification, render live progress bars, and confirm byte-for-byte delivery.

---

## Milestone 1 — LAN Demo Guide (Presentation Checkpoint)

This runbook describes how to execute a live, direct peer-to-peer file transfer between **two separate laptops** on the same local area network (Wi-Fi or Ethernet).

### Important Networking Note
`localhost` refers only to the local machine. Laptop B cannot reach Laptop A via `ws://localhost:8000`. You must specify Laptop A's actual local IP address (e.g. `192.168.1.45`).

### Step 1: Discover Laptop A's LAN IP Address
On **Laptop A**, find its local IP:
- **Linux**: `ip -br addr show` or `hostname -I`
- **macOS**: `ipconfig getifaddr en0` (or `en1` for Wi-Fi)
- **Windows**: `ipconfig` (look for `IPv4 Address`)

*Example IP:* `192.168.1.45`

### Step 2: Start Signaling Server (on Laptop A)
On **Laptop A**, run the signaling service bound to `0.0.0.0`:
```bash
python -m backend.signaling.server --host 0.0.0.0 --port 8000
```
> **Firewall Note**: If prompted, allow incoming connections on port 8000 (e.g. `sudo ufw allow 8000/tcp` on Linux).

### Step 3: Start Sender (on Laptop A)
Open a new terminal on **Laptop A** and run the sender, pointing to its LAN IP:
```bash
python -m backend.cli.peer --role send --file ./sample_presentation.bin --signaling-url ws://192.168.1.45:8000/ws
```
Laptop A will output:
```
============================================================
  ROOM CREATED: 8K7MYZ
  Share this 6-character room code with the receiving peer.
  Ready to send: sample_presentation.bin (5,242,880 bytes)
  Waiting for peer to join...
============================================================
```

### Step 4: Start Receiver (on Laptop B)
On **Laptop B** (connected to the same Wi-Fi / LAN), run:
```bash
python -m backend.cli.peer --role receive --room-code 8K7MYZ --output-dir ./received_files --signaling-url ws://192.168.1.45:8000/ws
```

### Step 5: Observe Live Transfer & Verify
1. **Rendezvous**: Both laptops report `PEER JOINED`.
2. **WebRTC**: Direct P2P connection is established (`connectionState: connected`).
3. **DataChannels**: `control` (JSON) and `data` (binary chunks) open.
4. **Transfer**: Chunks stream sequentially with per-chunk SHA-256 hashing and live ASCII progress bars.
5. **Validation**: Both sides display the completion summary table with identical whole-file SHA-256 hashes.

To independently verify on Linux / macOS:
```bash
# On Laptop A:
sha256sum ./sample_presentation.bin

# On Laptop B:
sha256sum ./received_files/sample_presentation.bin
```

---

## Running the Automated Test Suite

Run the complete test suite with `pytest`:

```bash
pytest -v
```

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
