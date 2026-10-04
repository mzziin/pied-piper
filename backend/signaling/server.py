"""FastAPI WebSocket signaling server for peer rendezvous and message relay."""

import argparse
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from backend.config import get_settings
from backend.signaling.messages import (
    ConnectedMessage,
    CreateRoomMessage,
    ErrorMessage,
    JoinRoomMessage,
    PeerJoinedMessage,
    RelayedSignalMessage,
    RoomCreatedMessage,
    RoomExpiredMessage,
    SignalMessage,
    parse_client_message,
)
from backend.signaling.rooms import Room, RoomRegistry, RoomState

logger = logging.getLogger(__name__)
settings = get_settings()

# Global room registry instance
registry = RoomRegistry(default_ttl_seconds=settings.room_ttl_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage signaling server lifecycle including background room TTL sweeping."""
    logger.info("Starting signaling server and TTL sweeper...")
    registry.start_ttl_sweeper(interval_seconds=1.0)
    yield
    logger.info("Stopping signaling server and TTL sweeper...")
    await registry.stop_ttl_sweeper()


app = FastAPI(
    title="Pied Piper Signaling Service",
    description="WebSocket rendezvous service for WebRTC signaling",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Service health check endpoint."""
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "pied-piper-signaling",
            "active_rooms": len(registry._rooms),
        }
    )


async def send_json_message(ws: WebSocket, message: Any) -> bool:
    """Helper to serialize and send a Pydantic message or dict to a WebSocket."""
    try:
        if hasattr(message, "model_dump"):
            payload = message.model_dump()
        else:
            payload = message
        await ws.send_text(json.dumps(payload))
        return True
    except Exception as exc:
        logger.warning("Failed to send message to websocket: %s", exc)
        return False


@app.websocket("/ws")
async def signaling_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint handling signaling room creation, joining, and relay."""
    await websocket.accept()
    current_room: Optional[Room] = None
    room_code: Optional[str] = None

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = parse_client_message(raw_text)
            except (json.JSONDecodeError, ValidationError) as err:
                logger.warning("Invalid client message received: %s", err)
                await send_json_message(
                    websocket,
                    ErrorMessage(reason=f"Malformed or invalid signaling message: {err}"),
                )
                continue

            # -----------------------------------------------------------------
            # Handle: create_room
            # -----------------------------------------------------------------
            if isinstance(msg, CreateRoomMessage):
                if current_room is not None:
                    await send_json_message(
                        websocket,
                        ErrorMessage(reason="Client is already in a room"),
                    )
                    continue

                room = await registry.create_room()
                room.add_peer(websocket)
                current_room = room
                room_code = room.code
                logger.info("Client created room %s", room_code)
                await send_json_message(
                    websocket,
                    RoomCreatedMessage(room_code=room_code),
                )

            # -----------------------------------------------------------------
            # Handle: join_room
            # -----------------------------------------------------------------
            elif isinstance(msg, JoinRoomMessage):
                if current_room is not None:
                    await send_json_message(
                        websocket,
                        ErrorMessage(reason="Client is already in a room"),
                    )
                    continue

                target_code = msg.room_code.strip().upper()
                room, error = await registry.join_room(target_code, websocket)
                if error or room is None:
                    logger.warning("Join room failed for code %s: %s", target_code, error)
                    await send_json_message(
                        websocket,
                        ErrorMessage(reason=error or "Failed to join room"),
                    )
                    continue

                current_room = room
                room_code = room.code
                logger.info("Client joined room %s. Notifying peers...", room_code)

                # Notify both peers in the room that rendezvous is complete
                peer_joined_msg = PeerJoinedMessage()
                for peer_ws in list(room.peers):
                    await send_json_message(peer_ws, peer_joined_msg)

            # -----------------------------------------------------------------
            # Handle: signal (SDP / ICE candidate relay)
            # -----------------------------------------------------------------
            elif isinstance(msg, SignalMessage):
                if current_room is None:
                    await send_json_message(
                        websocket,
                        ErrorMessage(reason="Must join or create a room before signaling"),
                    )
                    continue

                other_peer = current_room.other_peer(websocket)
                if other_peer is None:
                    logger.warning("Signal received in room %s but no other peer is present", room_code)
                    await send_json_message(
                        websocket,
                        ErrorMessage(reason="No peer in room to receive signal"),
                    )
                    continue

                # Relaying opaque payload verbatim
                relay_msg = RelayedSignalMessage(payload=msg.payload)
                await send_json_message(other_peer, relay_msg)

            # -----------------------------------------------------------------
            # Handle: connected (early room expiration on WebRTC success)
            # -----------------------------------------------------------------
            elif isinstance(msg, ConnectedMessage):
                if current_room is not None and room_code is not None:
                    await registry.mark_connected(room_code)
                    logger.info("WebRTC connection reported for room %s", room_code)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected (room: %s)", room_code)
    except Exception as exc:
        logger.error("Unexpected error in signaling websocket: %s", exc, exc_info=True)
    finally:
        if current_room is not None and room_code is not None:
            current_room.remove_peer(websocket)
            other_peer = current_room.other_peer(websocket)

            # If disconnected before reaching CONNECTED, expire room immediately
            if current_room.state == RoomState.WAITING_FOR_PEER:
                await registry.expire_room(room_code)
                if other_peer is not None:
                    await send_json_message(
                        other_peer,
                        RoomExpiredMessage(),
                    )


def main() -> None:
    """Run signaling server standalone via uvicorn."""
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="pied-piper-signaling",
        description="Pied Piper WebSocket Signaling Server",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=settings.signaling_host,
        help=f"Host to bind signaling server to (default: {settings.signaling_host})",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=settings.signaling_port,
        help=f"Port to bind signaling server to (default: {settings.signaling_port})",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=settings.log_level.lower(),
        help="Logging level (default: info)",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("       Pied Piper — WebSocket Signaling Service")
    print("=" * 60)
    print(f"  Binding Host:     {args.host}")
    print(f"  Binding Port:     {args.port}")
    print(f"  WebSocket URL:    ws://{args.host}:{args.port}/ws")
    print(f"  Health Endpoint:  http://{args.host}:{args.port}/health")
    print(f"  Room TTL:         {settings.room_ttl_seconds}s")
    print("=" * 60 + "\n")

    uvicorn.run(
        "backend.signaling.server:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
