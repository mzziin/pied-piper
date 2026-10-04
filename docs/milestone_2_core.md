# Milestone 2 — Core System Maturity (Phases 7–18)

## Purpose & Scope
This document is implementation-ready detail for Phases 7 through 18: taking the
LAN-validated foundation from Milestone 1 through to a resumable, performant,
securely hardened, desktop-integrated, production-ready system. Every protocol
mechanic referenced below (wire format, chunking, ACK/window, resume,
persistence) is fully specified in `docs/protocol_spec.md` — this document
describes *how and when* to build and wire up that already-designed protocol,
phase by phase.

Antigravity continues to implement **strictly phase-by-phase**, pausing after
each phase for human review and testing, exactly as in Milestone 1.

---

## Phase 7 — Application-Level Transfer Protocol

**Objective:** Replace Phase 5's minimal stop-and-wait, single-file protocol
subset with the full protocol defined in `docs/protocol_spec.md`: complete
message set, batch-capable framing, and the binary data-frame format.

**Why this phase exists:** Phase 5 deliberately implemented a strict subset.
This phase generalizes it to the final wire format without throwing away the
working sender/receiver/chunking code — it's an extension, not a rewrite.

**Prerequisites:** Phase 6 (LAN checkpoint) complete and passing.

**Scope / Tasks:**
- `backend/protocol/framing.py`: implement the full control message set from
  `protocol_spec.md` §3.1 (`transfer_offer`/`accept`/`reject`, `file_start`,
  `chunk_ack`/`chunk_nack`, `file_complete`, `transfer_complete`,
  `transfer_failed`, `window_update`, `ping`/`pong`, `resume_request`/
  `resume_offset`). Validate every incoming message against an explicit schema
  (reject unknown/malformed types cleanly — ties into Phase 16 hardening but
  should not be deferred entirely, since malformed input handling is core
  protocol robustness).
- `backend/protocol/chunking.py`: extend to the binary data-frame format from
  `protocol_spec.md` §3.2 (16-byte `file_id` + 8-byte `chunk_index` + 4-byte
  `payload_length` header). Update both sender-side framing and receiver-side
  parsing.
- Update `backend/transfer/sender.py` / `receiver.py` to speak the full
  message set, still operating in single-file, sequential mode for now
  (sliding window arrives in Phase 8) — this phase is about wire-format
  completeness, not yet flow control.
- Extend `transfer_offer` to accept a `files` array (batch-capable framing),
  even though the CLI still only ever populates it with one file until batch
  support is exposed at the CLI/API level (not required in this phase).

**Expected Output:** The same Phase 6 LAN transfer scenario now runs over the
full message set instead of the Phase 5 minimal subset, with identical
user-visible behavior.

**Acceptance Criteria:**
- Every message type in `protocol_spec.md` §3.1 has a corresponding
  encode/decode implementation and at least one round-trip test.
- Malformed control messages (missing fields, wrong types) are rejected with a
  clear error, not a crash.
- Binary data frames are parsed correctly at chunk-size boundaries and for the
  final (possibly short) chunk of a file.
- End-to-end file transfer (single file, same LAN setup as Phase 6) still
  succeeds and verifies correctly.

**Deferred:** Sliding window (Phase 8), actual batch/multi-file execution at
the CLI level, resume, persistence beyond what Phase 5 already had.

---

## Phase 8 — Large-File Streaming and Flow Control

**Objective:** Implement the sliding-window ACK scheme from `protocol_spec.md`
§5, replacing stop-and-wait, with bounded-memory streaming from disk.

**Why this phase exists:** This is what makes multi-GB and eventually
100s-of-GB files practical — throughput must not be hostage to per-chunk
round-trip latency, and memory must stay bounded regardless of file size.

**Prerequisites:** Phase 7 complete and tested.

**Scope / Tasks:**
- `backend/protocol/window.py`: implement sender-side window state (`base`,
  `next_chunk_to_send`, configurable `window_size` from
  `SLIDING_WINDOW_SIZE`), exactly as described in `protocol_spec.md` §5.
- Sender: read-ahead buffer bounded to `window_size` chunks; pause reading
  further chunks from disk when the buffer is full (backpressure); resume
  reading as `chunk_ack`s slide the window forward.
- Receiver: continue writing/verifying chunks strictly in order (no
  out-of-order acceptance needed, per the rationale in `protocol_spec.md` §5,
  since SCTP already guarantees order).
- Implement `window_update` handling for runtime window resizing (used later
  in Phase 14 tuning; wire it up now while touching this code).
- Benchmark informally (not full Phase 14 rigor) with a large local file
  (e.g., 1–5 GB) to confirm memory stays flat regardless of file size —
  actual streaming validation, not just code review.

**Expected Output:** A multi-GB file transfers successfully with memory usage
that does not scale with file size (verify with a system monitor during a
test transfer).

**Acceptance Criteria:**
- Memory footprint during transfer of a large file is bounded and roughly
  constant (proportional to `window_size × chunk_size`, not file size).
- Throughput noticeably improves over Phase 5/7's stop-and-wait baseline on a
  LAN (measure informally; formal benchmarking is Phase 14).
- Window correctly bounds in-flight chunks — verify via test that the sender
  never has more than `window_size` unACKed chunks outstanding.

**Deferred:** Checkpointing/persistence (Phase 9/10), resume (Phase 12),
formal performance tuning (Phase 14).

---

## Phase 9 — Progress and Checkpointing

**Objective:** Implement receiver-confirmed checkpointing: the durable
"highest verified, gap-free chunk" pointer described in `protocol_spec.md`
§6, and surface real progress reporting.

**Why this phase exists:** Before persistence (Phase 10) can make resume
possible, the system needs a correct, race-free definition of "how much of
this file is safely, verifiably done" — that's the checkpoint concept.

**Prerequisites:** Phase 8 complete and tested.

**Scope / Tasks:**
- Receiver: after each chunk is written to its correct on-disk offset and
  hash-verified, update `highest_verified_chunk` per `protocol_spec.md` §6.1
  — in memory first (SQLite persistence lands fully in Phase 10, but the
  in-memory version of this bookkeeping starts now so Phase 10 is additive).
- Implement the write-then-confirm ordering described in §6.2: the disk write
  must complete before the checkpoint pointer advances.
- Progress reporting: implement the derived-progress model from
  `protocol_spec.md` §11 (`highest_verified_chunk + 1` out of `total_chunks`
  on the receiver; `base` out of `total_chunks` on the sender). Emit this as
  an internal event/callback (the real desktop-facing API arrives in Phase 17,
  but the internal event shape should be decided now so it doesn't change
  later).
- CLI: print live progress percentage during transfer using this model
  (upgrade from Phase 5's simpler ACK-count-based progress).

**Expected Output:** Accurate, monotonically-increasing progress percentage
displayed during a transfer, based on receiver-confirmed (not merely sent)
data.

**Acceptance Criteria:**
- Checkpoint pointer only advances when chunks are both written to disk and
  hash-verified, with no gaps — inject a deliberate out-of-order/dropped
  scenario in a test to confirm the pointer doesn't falsely advance.
- Progress percentage is monotonic (never decreases during a normal,
  uninterrupted transfer).
- Progress reflects receiver state, not sender send-count, per the design
  decision in `protocol_spec.md` §11.

**Deferred:** SQLite persistence (Phase 10 — this phase's checkpoint logic is
correct but still in-memory only), resume itself (Phase 12).

---

## Phase 10 — Persistent Transfer State

**Objective:** Implement the full SQLite schema from `protocol_spec.md` §6.1
on both sender and receiver, making Phase 9's checkpoint state (and all
transfer/file/chunk metadata) durable across process restarts.

**Why this phase exists:** This is the foundation resume (Phase 12) and
reconnection (Phase 11) depend on — without durable state, "resume after full
process death" is impossible by definition.

**Prerequisites:** Phase 9 complete and tested.

**Scope / Tasks:**
- `backend/transfer/state_store.py`: implement the exact schema from
  `protocol_spec.md` §6.1 (`transfers`, `files`, `chunk_hashes` tables) using
  the standard library `sqlite3` module (async-wrapped via a thread executor,
  since `sqlite3` is not natively async-friendly — do not introduce a new
  dependency for this).
- Wire persistence into the sender: on starting a transfer, insert a
  `transfers` row (`role='sender'`) and a `files` row; as each chunk is read
  and hashed, insert into `chunk_hashes` (`verified=0`).
- Wire persistence into the receiver: on accepting a transfer, insert
  `transfers` (`role='receiver'`) and `files` rows; as each chunk is verified
  and written, insert/update `chunk_hashes` (`verified=1`) and advance
  `files.highest_verified_chunk`, per the write-then-commit ordering in
  `protocol_spec.md` §6.2.
- Migration/startup: on process start, ensure the schema exists (idempotent
  `CREATE TABLE IF NOT EXISTS`) — no separate migration framework needed at
  this stage.
- Add a basic CLI utility or debug command to inspect persisted transfer
  state (helpful for testing Phase 11/12 later).

**Expected Output:** After a transfer completes or is interrupted, inspecting
the SQLite database shows accurate, consistent state matching what actually
happened (and, for partial transfers, matching what's actually on disk).

**Acceptance Criteria:**
- Killing the receiver process mid-transfer (`kill -9` equivalent, not a
  graceful shutdown) and inspecting its SQLite DB shows a `highest_verified_chunk`
  that matches what's verifiably on disk in the partial file (test this
  explicitly — read the partial file's bytes at that checkpoint and
  independently verify the hash).
- Completed transfers show `status='completed'` on both sides.
- Schema creation is idempotent (running twice doesn't error).

**Deferred:** Actually using this state to resume or reconnect (Phases 11–12
consume it) — this phase only makes state durable, it doesn't yet act on it
after interruption.

---

## Phase 11 — Reconnection

**Objective:** Implement disconnect detection and the reconnect grace-window
mechanism from `protocol_spec.md` §10: detecting a dropped connection and
re-establishing a fresh WebRTC connection tied to the same `transfer_id`.

**Why this phase exists:** Real networks drop connections. Before resume
(Phase 12) can mean anything, peers need a way to actually find each other
again after a disconnect, without requiring a brand-new manual room-code
exchange every time.

**Prerequisites:** Phase 10 complete and tested.

**Scope / Tasks:**
- `backend/transport/peer_connection.py`: add heartbeat sending/monitoring —
  `ping` every `HEARTBEAT_INTERVAL_SECONDS` (5s default), `pong` in response,
  and a timeout check (`HEARTBEAT_TIMEOUT_SECONDS`, 15s default) — per
  `protocol_spec.md` §10.
- Monitor `RTCPeerConnection.connectionState` for `disconnected`/`failed` as
  the second detection signal (already logged since Phase 3; now it triggers
  behavior, not just logging).
- `backend/signaling/rooms.py`: add the `create_reconnect_room` message type
  and short-TTL (`RECONNECT_GRACE_SECONDS`, 120s default) room variant
  described in `protocol_spec.md` §10.
- On detecting disconnection, the detecting peer requests a reconnect room
  tied to the existing `transfer_id`; for the CLI reference peer, automate
  full reconnection (both CLI processes already know the shared
  `transfer_id` and can coordinate the reconnect room code automatically
  without manual re-entry, since this is a same-test-harness scenario) —
  document that a real desktop UX might need to surface this to the user
  differently (Phase 17 concern, not this phase's).
- On the new room, perform a full fresh SDP/ICE renegotiation (not ICE
  restart), re-establish both DataChannels, per `protocol_spec.md` §10.
- After DataChannels reopen, do **not** yet resume file transfer — that's
  explicitly Phase 12. This phase's acceptance is just "peers successfully
  reconnect and re-establish channels."

**Expected Output:** Killing the network connection (e.g., disabling Wi-Fi
briefly, or simulating via a dropped connection in test) mid-transfer results
in both peers detecting the drop and successfully re-establishing a new
WebRTC connection within the grace window.

**Acceptance Criteria:**
- Both heartbeat-timeout and ICE-state detection paths are independently
  tested (simulate each separately).
- Reconnection succeeds within `RECONNECT_GRACE_SECONDS` in a LAN test.
- If reconnection does not happen within the grace window, the transfer is
  marked appropriately (e.g., `paused` status in SQLite) rather than left in
  an ambiguous state.
- Reconnecting after a full process restart (not just a network blip) also
  succeeds, using the persisted `transfer_id` from Phase 10's SQLite state.

**Deferred:** Actually resuming the file transfer itself (Phase 12).

---

## Phase 12 — Resume

**Objective:** Implement the full receiver-authoritative resume algorithm
from `protocol_spec.md` §7 on top of Phase 11's reconnection mechanism.

**Why this phase exists:** This is the payoff of Phases 9–11 — the system can
now actually continue an interrupted transfer from where it left off, rather
than just reconnecting and having nothing to do with that connection.

**Prerequisites:** Phase 11 complete and tested.

**Scope / Tasks:**
- On reconnection (Phase 11) for a `transfer_id` with incomplete persisted
  state, trigger the resume flow immediately:
  - Receiver re-validates its `highest_verified_chunk` via the single-chunk
    re-hash sanity check (`protocol_spec.md` §7 step 1).
  - Receiver sends `resume_offset` with `resume_from_chunk =
    highest_verified_chunk + 1`.
  - Sender seeks to that offset in the source file and sends `file_start`
    with the resume `start_chunk_index`, then resumes normal sliding-window
    sending.
- Implement the sender-side seek logic in `backend/transfer/sender.py`
  (open the source file, seek to `resume_from_chunk * chunk_size`, continue
  reading sequentially from there).
- Implement receiver-side seek logic in `backend/transfer/receiver.py` (open
  the existing partial `dest_path` file in `r+b`, seek writes to the correct
  offset, continuing to verify and checkpoint as normal).
- CLI: no new flags needed — resume should be automatic and transparent
  whenever a matching persisted incomplete transfer is found after
  reconnection.

**Expected Output:** An intentionally interrupted large-file transfer (kill
one CLI process mid-transfer, restart it) resumes from very close to where it
left off — not from byte zero — and completes successfully with a verified
whole-file hash.

**Acceptance Criteria:**
- This is the explicit "resume" test called out in `PROJECT.md` §9 testing
  strategy: interrupt a transfer, reconnect, confirm it resumes (not
  restarts) and completes with correct final hash.
- Resume works both after a network-blip-only interruption and after killing
  and restarting the receiver process entirely.
- The receiver's stated resume offset is always what the sender obeys — write
  a test that would fail if the sender ever used its own idea of progress
  instead.

**Deferred:** Corruption-triggered rollback specifically (Phase 13, though
resume's re-validation step already contains the seed of this).

---

## Phase 13 — Recovery and Integrity Hardening

**Objective:** Implement the rollback-to-checkpoint mechanism from
`protocol_spec.md` §8 for corruption detected either mid-transfer
(`chunk_nack`) or during resume-time re-validation, plus the non-recoverable
failure cases from §9.

**Why this phase exists:** Phase 12 handles the "clean" resume case.
Real-world partial files can also be corrupted (truncated writes, disk
errors, tampering) — this phase makes the system safely detect and recover
from that without ever requiring a full restart for large files.

**Prerequisites:** Phase 12 complete and tested.

**Scope / Tasks:**
- Implement `chunk_nack` handling: on hash mismatch for a freshly received
  chunk, the receiver does not crash or abort — it logs the mismatch and
  relies on the resume flow's next occurrence to walk back correctly (per
  `protocol_spec.md` §8, rollback IS the resume algorithm's error path, not a
  separate code path) — verify this shared-code-path design holds in
  implementation, not just in spec.
- Implement the backward-walk re-verification described in §7 step 1 /
  §8: on finding a hash mismatch at `highest_verified_chunk`, decrement and
  re-check the previous chunk, repeating until a verified match is found;
  update `highest_verified_chunk` (and by extension, the next
  `resume_offset`) accordingly.
- Implement the non-recoverable failure detection from §9: before resuming a
  send, sender checks current source file size/mtime against the original
  offer's declared size; mismatch triggers `transfer_failed`, not a corrupted
  resume attempt. Also handle destination-not-writable on the receiver side
  similarly.
- Add cleanup logic: on `transfer_failed`, leave the partial file in place
  (do not delete — useful for debugging) but mark `files.status = 'failed'`
  and `transfers.status = 'failed'` in SQLite so it's not mistakenly picked
  up by a future resume attempt for the same `transfer_id`.

**Expected Output:** Deliberately corrupting a few bytes in a receiver's
partial file, then triggering a reconnect/resume, results in the system
correctly detecting the corruption, rolling back to the last good chunk, and
successfully completing the transfer from there — without a full restart.

**Acceptance Criteria:**
- Corruption injection test: manually flip bytes in a partial received file,
  trigger resume, confirm the system detects it and rolls back rather than
  either (a) silently accepting corrupted data or (b) restarting from zero.
- Source-file-changed test: modify/replace the sender's source file mid-pause,
  attempt resume, confirm `transfer_failed` is raised rather than sending
  wrong data under the original file's identity.
- No test scenario in this phase should result in either a hang or a crash —
  every failure path terminates in a clean, logged `transfer_failed` or
  successful rollback+resume.

**Deferred:** Performance tuning (Phase 14), TURN/real-network operation
(Phase 15), security hardening beyond what's needed for correctness here
(Phase 16 covers adversarial/malicious input specifically).

---

## Phase 14 — Performance Engineering

**Objective:** Measure and tune chunk size, sliding window size, and file I/O
buffering, using the configurability already built into Phases 4–8.

**Why this phase exists:** Earlier phases prioritized correctness with
reasonable defaults (16 KB chunks, window of 32). This phase is dedicated to
actually measuring whether those defaults are good and tuning them with data.

**Prerequisites:** Phase 13 complete and tested.

**Scope / Tasks:**
- Build a lightweight benchmarking harness (CLI flag or standalone script)
  that runs a local (or LAN) transfer of a fixed test file across a matrix of
  `CHUNK_SIZE_BYTES` and `SLIDING_WINDOW_SIZE` values, recording throughput
  (MB/s) and peak memory usage.
- Test at minimum: chunk sizes of 4 KB, 16 KB (current default), 64 KB, 256 KB;
  window sizes of 8, 32 (current default), 128.
- Identify and address any I/O bottlenecks found (e.g., switch from
  synchronous file reads to `asyncio`-friendly chunked reads via a thread
  executor if blocking I/O is found to stall the event loop).
- Update the `.env.example` defaults if benchmarking shows a materially
  better default than the Phase 1 placeholder values — document the
  reasoning for the new defaults in this doc or a benchmarking results note.
- Confirm the `window_update` message (already wired in Phase 8) allows
  runtime tuning without a restart, and that this doesn't destabilize an
  in-progress transfer.

**Expected Output:** A short benchmark report (can be a markdown note, doesn't
need to be a separate formal document) showing throughput/memory across the
tested matrix, and updated defaults if warranted.

**Acceptance Criteria:**
- Benchmark harness runs repeatably and produces comparable numbers across
  runs.
- Chosen defaults are justified by actual measured data, not just left as
  Phase 1 placeholders without re-examination.
- No regression in correctness (Phase 12/13 resume and rollback tests still
  pass) after any tuning changes.

**Deferred:** Real (non-LAN) network condition testing (Phase 15) — this
phase's benchmarks are LAN/local, not simulating latency or packet loss.

---

## Phase 15 — Real Network Connectivity

**Objective:** Validate direct P2P operation across different networks (not
just same-LAN), implement TURN relay fallback for when direct connectivity
fails, and resolve the one legitimate TBD in the entire system: which TURN
provider/service to use.

**Why this phase exists:** Everything so far has been validated same-machine
or same-LAN, where direct P2P is trivial. Real-world usage means NATs,
firewalls, and networks where direct P2P fails — TURN relay is the
fallback for that case, per `PROJECT.md` §4 and this being the explicit,
sanctioned point of deferred decision-making.

**Prerequisites:** Phase 14 complete.

**Scope / Tasks:**
- **Resolve the TURN TBD:** evaluate TURN provider options (self-hosted
  `coturn` vs. a hosted TURN service) based on cost, ease of setup for a
  class/personal project, and reliability. Document the chosen provider and
  configuration in `.env.example` (`TURN_URL`, `TURN_USERNAME`,
  `TURN_CREDENTIAL`) and in this doc once decided — this is the only place in
  the entire architecture where a "TBD" was ever legitimate, and it is
  resolved here, not left open beyond this phase.
- Update `backend/transport/peer_connection.py`'s `RTCConfiguration` to
  include both STUN and TURN servers (`RTCIceServer` entries for each),
  letting `aiortc`'s built-in ICE candidate gathering/prioritization handle
  falling back to relay automatically when direct candidates fail to connect
  — do not hand-roll TURN protocol logic (`PROJECT.md` §4 explicitly rules
  this out: "Do not implement TURN from scratch").
- Test scenario: two peers on genuinely different networks (e.g., one on home
  Wi-Fi, one on mobile hotspot/cellular, or two different physical
  locations), confirming that direct P2P succeeds when possible and TURN
  relay succeeds when it isn't (can force TURN-only testing by temporarily
  restricting STUN/direct candidates for test purposes).
- Log which connection type (direct vs. relayed) was actually used for a
  given session — valuable observability info per `PROJECT.md` §10.

**Expected Output:** Successful file transfer between two peers on different,
real-world networks, using direct P2P where possible and TURN relay as an
automatic fallback where not, with no manual network configuration by the
user beyond the standard room-code flow.

**Acceptance Criteria:**
- At least one successful direct P2P transfer across different networks
  (not just same-LAN).
- At least one successful TURN-relayed transfer where direct P2P is
  deliberately made unavailable.
- Connection type (direct/relayed) is logged and could be surfaced to the
  user later if desired.
- No changes needed to the transfer protocol itself (Phases 7–13) — this
  phase is purely about the transport/ICE layer, confirming the earlier
  architectural separation held.

**Deferred:** Nothing protocol-related — this phase is transport-layer only.
Security hardening (Phase 16) is separate and still pending.

---

## Phase 16 — Security and Hardening

**Objective:** Systematically address the security requirements from
`PROJECT.md` §10 and the original prompt's security constraints: input
validation, filename sanitization, path traversal prevention, resource
limits, and secure signaling.

**Why this phase exists:** Earlier phases handled correctness against
well-behaved peers. This phase specifically hardens against malformed,
malicious, or adversarial input — necessary before any exposure beyond a
trusted LAN/known-peer scenario.

**Prerequisites:** Phase 15 complete.

**Scope / Tasks:**
- **Filename sanitization audit:** confirm (and add tests for) that no
  filename from a `transfer_offer` can ever result in a write outside the
  designated output directory — reject/strip path separators, `..`
  components, absolute paths, and null bytes; resolve final paths via
  `pathlib.Path.resolve()` and verify the result is still inside the
  configured output directory before any write.
- **Signaling input validation:** ensure every signaling message (Phase 2)
  and every protocol control message (Phase 7) is validated against its
  expected schema before use — reject anything malformed with a clear error,
  never trust field types/presence implicitly. Fuzz-test with deliberately
  malformed JSON payloads.
- **Resource limits:** cap maximum concurrent rooms (signaling server),
  maximum message size on `control` (should never need to be large — file
  data goes on `data`), and maximum `chunk_size`/`window_size` values
  accepted via `window_update` (prevent a malicious peer from requesting an
  absurd window size to exhaust memory).
- **Room-code security review:** confirm the 6-character keyspace (§Phase 2)
  combined with the 15-minute TTL provides adequate brute-force resistance;
  add basic rate-limiting on join attempts per IP if not already present, to
  prevent room-code guessing via rapid automated attempts.
- **Malformed binary frame handling:** ensure the `data` channel binary
  parser (Phase 7) rejects frames with inconsistent `payload_length` (e.g.,
  claimed length exceeding actual received bytes, or exceeding configured
  `chunk_size` maximum) rather than reading out of bounds or hanging.

**Expected Output:** A documented security review pass with specific tests
demonstrating each hardening measure actually blocks the corresponding attack
(path traversal attempt rejected, malformed message rejected, oversized
window request rejected, etc.).

**Acceptance Criteria:**
- Path traversal test (filename like `../../etc/passwd`) is rejected, not
  silently sanitized-and-allowed into an unexpected location.
- Fuzzed/malformed control and signaling messages never crash the server or
  peer process — always a clean rejection.
- Resource limit tests confirm caps are enforced (e.g., requesting an
  oversized window is capped or rejected, not honored).
- No regression in Phase 1–15 functional tests.

**Deferred:** Nothing further — this is the last purely defensive phase
before integration and production packaging.

---

## Phase 17 — Desktop Integration

**Objective:** Finalize and implement the async Python API boundary
(`backend/api/transfer_api.py`) that the desktop application will consume, per
the architectural decision in `PROJECT.md` §5/§7 (async API with event
callbacks, not REST/IPC).

**Why this phase exists:** This is the actual integration point between
Antigravity's backend and the human teammate's desktop application — the
seam that's been designed for since Phase 1's module boundaries but never
built until every underlying capability (transfer, resume, reconnection,
security) is solid.

**Prerequisites:** Phase 16 complete. (Note: per `PROJECT.md` §3/§14, the
desktop developer may have been designing against the *shape* of this
boundary in parallel since early phases — this phase is about finalizing and
implementing it concretely, not inventing it from scratch at the last
minute.)

**Scope / Tasks:**
- Design and implement `backend/api/transfer_api.py` exposing, at minimum:
  - `async def create_room() -> str` (returns room code)
  - `async def join_room(room_code: str) -> None`
  - `async def offer_transfer(file_paths: list[Path]) -> str` (returns
    `transfer_id`)
  - `async def accept_transfer(transfer_id: str) -> None`
  - `async def reject_transfer(transfer_id: str, reason: str) -> None`
  - Event callback registration, e.g.
    `on_progress(callback: Callable[[ProgressEvent], None])`,
    `on_connection_state_changed(...)`, `on_transfer_completed(...)`,
    `on_transfer_failed(...)`, `on_reconnecting(...)` — covering every state
    transition the desktop UI would plausibly need to reflect.
  - `async def pause_transfer(transfer_id: str) -> None` /
    `async def cancel_transfer(transfer_id: str) -> None` (map onto existing
    `paused`/`failed` states from the SQLite schema).
- This API wraps and orchestrates the existing signaling/transport/transfer
  layers — it must not duplicate their logic, only sequence and expose it.
- Write a small **reference integration example** (not a real GUI — a short
  script) demonstrating a hypothetical desktop app calling this API, to
  validate the boundary is actually usable before the real desktop code
  depends on it.
- Document the API thoroughly (docstrings + a dedicated section in this repo,
  e.g. `docs/api_reference.md` if warranted, or expand `PROJECT.md`) so the
  desktop developer can integrate without needing to read backend internals.
- The CLI reference peer (`backend/cli/peer.py`) should ideally be refactored
  to itself consume this same `transfer_api.py` boundary rather than calling
  lower layers directly — this both validates the API design and keeps one
  source of truth for "how to drive a transfer," rather than two divergent
  code paths.

**Expected Output:** A documented, tested async API that a desktop application
can import and drive an entire transfer lifecycle through — verified via the
reference integration example and the refactored CLI both working through it.

**Acceptance Criteria:**
- CLI reference peer, refactored to use `transfer_api.py`, still passes all
  functional tests from Phases 1–16 (i.e., the refactor didn't regress
  behavior).
- Reference integration example successfully drives a full transfer
  lifecycle (create room → connect → offer → accept → progress events →
  completion) using only the public API surface.
- API is fully async (no blocking calls on the event loop) and event
  callbacks fire correctly for every major state transition.
- Desktop developer (human teammate) confirms the API shape is workable for
  their planned UI (a real cross-check, not just internal review).

**Deferred:** Nothing backend-related — the actual desktop UI implementation
remains entirely out of Antigravity's scope, as stated throughout.

---

## Phase 18 — Production Readiness

**Objective:** Packaging, deployment configuration, observability polish, and
final documentation for a production-usable (or at least demo/release-ready)
system.

**Why this phase exists:** A functionally complete system still needs
packaging and operational polish before it's genuinely usable outside a
development environment.

**Prerequisites:** Phase 17 complete.

**Scope / Tasks:**
- Packaging: define how the backend is distributed/run — at minimum, a clean
  `pip install -r requirements.txt` + documented run commands; optionally
  package as an installable module (`pyproject.toml` with proper
  `[project]` metadata) if useful for the desktop app's build process to
  depend on.
- Deployment configuration: document how to run the signaling service in a
  more permanent location (not just "on one of the laptops," if the project
  has matured to wanting a persistent deployment) — e.g., a small always-on
  machine or basic cloud VM, using the existing `.env`-based config with
  production values (`wss://` instead of `ws://`, production TURN
  credentials from Phase 15).
- Observability polish: review all logging added since Phase 1 for
  consistency (structured/JSON-capable per `PROJECT.md` §10), ensure log
  levels are sensible (not everything at `INFO`), and confirm no file
  contents or sensitive data (TURN credentials, room codes at volume) are
  ever logged at a level that would leak in production logs.
- Final documentation pass: ensure `README.md`, `PROJECT.md`, and all
  `docs/*.md` files are internally consistent and reflect the actual
  final implementation (not stale early-phase assumptions) — this is a
  documentation audit, not new document creation.
- Final full regression pass: run the complete test suite (Phases 1–17) end
  to end to confirm nothing has silently regressed across the full phase
  history.

**Expected Output:** A repository that a new developer (or the teammate, or a
grader/reviewer) could clone, configure via `.env`, and run successfully
following only the README — with all documentation accurate and the full
system demonstrably working end-to-end (signaling → connection → transfer →
resume → desktop integration boundary).

**Acceptance Criteria:**
- Fresh clone + documented setup steps succeed without undocumented manual
  fixes.
- Full regression suite passes.
- Documentation review finds no contradictions between `PROJECT.md`,
  `docs/milestone_1_lan.md`, `docs/protocol_spec.md`, and
  `docs/milestone_2_core.md`.
- No sensitive data appears in logs at default log levels.

**Deferred:** Nothing — this is the final phase of the defined roadmap.

---

## Cross-Phase Notes

**Dependency summary:** Phases 7→13 are strictly sequential (each builds
directly on persisted/negotiated state from the previous one). Phase 14
(performance) and Phase 16 (security) could in principle be reordered
relative to each other, but both require Phase 13's stable resume/rollback
behavior as a base, so neither should run before Phase 13 completes. Phase 15
(real network/TURN) is independent of Phases 14/16 in principle but is kept
in its documented order since it's the natural point to resolve the TURN TBD
before hardening (Phase 16) and integration (Phase 17) assume a finished
transport story.

**Parallel work:** Throughout Phases 7–16, the desktop developer can continue
UI/UX work against the *stable, already-fixed shape* of the Phase 17 API
boundary (defined conceptually since `PROJECT.md` §5) without waiting for
backend completion. Phase 17 itself is the one phase that requires actual
coordination between both developers (API shape validation), not just
parallel independent work.

**Review cadence:** As with Milestone 1, Antigravity pauses after each phase
above for human review and testing before proceeding to the next — this
applies through Phase 18.
