"""Command-line reference peer client for Pied Piper.

Full implementation of Phase 5 single-file transfer protocol:
- Sender creates room, connects WebRTC, and streams file sequentially over DataChannels.
- Receiver joins using room code, connects WebRTC, verifies per-chunk & whole-file SHA-256,
  and saves verified file to the specified output directory.
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

from backend.config import Settings, get_settings
from backend.signaling.client import SignalingClient, SignalingError
from backend.transfer.receiver import FileReceiver
from backend.transfer.sender import FileSender, IntegrityError, TransferError, TransferSummary
from backend.transport.data_channels import DataChannelError
from backend.transport.peer_connection import (
    PeerConnectionError,
    establish_webrtc_connection,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pied-piper-peer")


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="pied-piper-peer",
        description="Pied Piper P2P File Transfer CLI Reference Peer",
    )
    parser.add_argument(
        "--role",
        "-r",
        choices=["send", "receive", "inspect"],
        required=True,
        help="Operating role for this peer ('send', 'receive', or 'inspect')",
    )
    parser.add_argument(
        "--signaling-url",
        "-s",
        type=str,
        default=None,
        help="WebSocket URL for the signaling service (defaults to config SIGNALING_URL)",
    )
    parser.add_argument(
        "--room-code",
        "-c",
        type=str,
        default=None,
        help="6-character room code to join (required for 'receive' role)",
    )
    parser.add_argument(
        "--file",
        "-f",
        type=Path,
        default=None,
        help="Path to file to send (required for 'send' role in file transfer mode)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("./received_files"),
        help="Directory to save received files (defaults to ./received_files)",
    )
    return parser


def format_progress_bar(percent: float, current: int, total: int, bar_length: int = 30) -> str:
    """Render an ASCII progress bar string."""
    filled_length = int(bar_length * percent / 100) if total > 0 else bar_length
    bar = "█" * filled_length + "░" * (bar_length - filled_length)
    return f"\r[Transfer] |{bar}| {percent:6.1f}% ({current}/{total} chunks)"


def print_transfer_summary(summary: TransferSummary, role: str) -> None:
    """Print formatted summary table of transfer results."""
    print("\n" + "=" * 62)
    print("           FILE TRANSFER COMPLETED SUCCESSFULLY!              ")
    print("=" * 62)
    print(f"  Role:             {role.upper()}")
    print(f"  File Name:        {summary.filename}")
    if summary.filepath:
        print(f"  Saved Path:       {summary.filepath.resolve()}")
    print(f"  File Size:        {summary.size_bytes:,} bytes")
    print(f"  Total Chunks:     {summary.total_chunks}")
    print(f"  SHA-256 Hash:     {summary.sha256}")
    print(f"  Duration:         {summary.duration_seconds:.2f}s")
    print(f"  Throughput:       {summary.throughput_mbps:.2f} Mbps")
    print("=" * 62 + "\n")


async def run_sender_flow(signaling_url: str, filepath: Path, settings: Settings) -> int:
    """Run sender workflow: room creation, WebRTC negotiation, and file streaming."""
    if not filepath.is_file():
        print(f"Error: File to send not found: {filepath}", file=sys.stderr)
        return 1

    logger.info("Connecting to signaling server: %s", signaling_url)
    async with SignalingClient(signaling_url) as client:
        room_code = await client.create_room(timeout=10.0)
        print("\n" + "=" * 60)
        print(f"  ROOM CREATED: {room_code}")
        print("  Share this 6-character room code with the receiving peer.")
        print(f"  Ready to send: {filepath.name} ({filepath.stat().st_size:,} bytes)")
        print("  Waiting for peer to join...")
        print("=" * 60 + "\n")

        await client.wait_for_peer(timeout=float(settings.room_ttl_seconds))
        print("\n[+] Peer joined. Establishing WebRTC connection & DataChannels...")

        pc_wrapper = await establish_webrtc_connection(
            role="send",
            signaling_client=client,
            settings=settings,
            timeout=30.0,
        )

        try:
            print(f"\n[+] Starting file transfer: '{filepath.name}'")

            def on_progress(pct: float, current: int, total: int) -> None:
                sys.stdout.write(format_progress_bar(pct, current, total))
                sys.stdout.flush()

            sender = FileSender(
                channels=pc_wrapper.channels,
                filepath=filepath,
                chunk_size=settings.chunk_size_bytes,
                progress_callback=on_progress,
            )

            summary = await sender.send(timeout=60.0)
            print()  # newline after progress bar
            print_transfer_summary(summary, role="send")

            # Allow final frames to flush before teardown
            await asyncio.sleep(0.3)
            return 0

        finally:
            await pc_wrapper.close()


async def run_receiver_flow(signaling_url: str, room_code: str, output_dir: Path, settings: Settings) -> int:
    """Run receiver workflow: room join, WebRTC negotiation, and verified file reception."""
    code = room_code.strip().upper()
    logger.info("Connecting to signaling server %s for room: %s", signaling_url, code)
    async with SignalingClient(signaling_url) as client:
        await client.join_room(code, timeout=10.0)
        print(f"\n[+] Joining room {code} on signaling server...")
        await client.wait_for_peer(timeout=float(settings.room_ttl_seconds))
        print("\n[+] Rendezvous confirmed. Establishing WebRTC connection & DataChannels...")

        pc_wrapper = await establish_webrtc_connection(
            role="receive",
            signaling_client=client,
            settings=settings,
            timeout=30.0,
        )

        try:
            print(f"\n[+] Awaiting file offer from sender (output directory: {output_dir.resolve()})...")

            def on_progress(pct: float, current: int, total: int) -> None:
                sys.stdout.write(format_progress_bar(pct, current, total))
                sys.stdout.flush()

            receiver = FileReceiver(
                channels=pc_wrapper.channels,
                output_dir=output_dir,
                progress_callback=on_progress,
            )

            summary = await receiver.receive(timeout=60.0)
            print()  # newline after progress bar
            print_transfer_summary(summary, role="receive")

            await asyncio.sleep(0.3)
            return 0

        finally:
            await pc_wrapper.close()


async def run_inspect_flow(settings: Settings) -> int:
    """Inspect persisted SQLite transfer state from the command line."""
    from backend.transfer.state_store import TransferStateStore
    store = TransferStateStore(settings.sqlite_path)
    await store.initialize()
    transfers = await store.list_transfers()
    print("\n" + "=" * 60)
    print(f"       Pied Piper — Persisted Transfer State ({settings.sqlite_path})")
    print("=" * 60)
    print(f"Total Transfers: {len(transfers)}\n")
    for t in transfers:
        f = await store.get_file_by_transfer(t["transfer_id"])
        print(f"Transfer ID: {t['transfer_id']} [{t['role'].upper()}] - Status: {t['status']}")
        if f:
            print(f"  File: {f['filename']} ({f['size_bytes']:,} bytes, {f['total_chunks']} chunks)")
            print(f"  Highest Verified Chunk: {f['highest_verified_chunk']} / {f['total_chunks'] - 1}")
            print(f"  Path: {f['file_path']}")
        print("-" * 60)
    print("=" * 60 + "\n")
    return 0


async def async_main(argv: Optional[List[str]] = None) -> int:
    """Async CLI entrypoint."""
    parser = create_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    signaling_url = args.signaling_url if args.signaling_url is not None else settings.signaling_url

    print(
        "============================================================",
        "\n              Pied Piper — CLI Reference Peer               \n"
        "============================================================",
        f"\nRole:             {args.role}",
        f"\nSignaling URL:    {signaling_url}",
    )
    if args.room_code:
        print(f"Room Code:        {args.room_code}")
    if args.file:
        print(f"File to Send:     {args.file}")
    if args.output_dir:
        print(f"Output Directory: {args.output_dir}")
    print("============================================================\n")

    try:
        if args.role == "send":
            if not args.file:
                print("Error: --file is required for 'send' role.", file=sys.stderr)
                return 1
            return await run_sender_flow(signaling_url, args.file, settings)

        elif args.role == "receive":
            if not args.room_code:
                print("Error: --room-code is required for 'receive' role.", file=sys.stderr)
                return 1
            return await run_receiver_flow(signaling_url, args.room_code, args.output_dir, settings)

        elif args.role == "inspect":
            return await run_inspect_flow(settings)

        else:
            print(f"Error: Unknown role '{args.role}'", file=sys.stderr)
            return 1

    except (SignalingError, PeerConnectionError, DataChannelError, TransferError, IntegrityError, OSError) as exc:
        print(f"\nTransfer Error: {exc}", file=sys.stderr)
        return 1
    except asyncio.CancelledError:
        print("\nOperation cancelled.", file=sys.stderr)
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    try:
        return asyncio.run(async_main(argv))
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
