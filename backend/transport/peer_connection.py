"""WebRTC RTCPeerConnection wrapper, STUN/ICE configuration, and connection handshake."""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from aiortc import (
    RTCConfiguration,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp

from backend.config import Settings, get_settings
from backend.signaling.client import SignalingClient, SignalingError
from backend.transport.data_channels import DataChannelManager

logger = logging.getLogger(__name__)


class PeerConnectionError(Exception):
    """Exception raised when a WebRTC transport error occurs."""
    pass


class PeerConnectionWrapper:
    """Wraps aiortc RTCPeerConnection and DataChannels, managing STUN, SDP, and ICE."""

    def __init__(self, settings: Optional[Settings] = None, role: str = "send") -> None:
        self.settings: Settings = settings if settings is not None else get_settings()
        self.role: str = role

        # Build STUN/TURN server configuration
        ice_servers: List[RTCIceServer] = []
        for stun_url in self.settings.stun_urls_list:
            ice_servers.append(RTCIceServer(urls=stun_url))

        if self.settings.turn_url:
            ice_servers.append(
                RTCIceServer(
                    urls=self.settings.turn_url,
                    username=self.settings.turn_username or None,
                    credential=self.settings.turn_credential or None,
                )
            )

        self.configuration = RTCConfiguration(iceServers=ice_servers)
        self.pc = RTCPeerConnection(configuration=self.configuration)

        self.channels = DataChannelManager()
        self._connected_event = asyncio.Event()
        self._failed_event = asyncio.Event()
        self._pending_candidates: List[Dict[str, Any]] = []
        self._logged_active_pair: bool = False

        # Setup DataChannels based on role
        if self.role == "send":
            self.channels.setup_offerer_channels(self.pc)
        else:
            self.channels.setup_answerer_channels(self.pc)

        # Attach connection state listeners for observability
        self._setup_listeners()

    def _setup_listeners(self) -> None:
        """Attach state change handlers to RTCPeerConnection."""
        @self.pc.on("icecandidate")
        def on_ice_candidate(candidate: Optional[RTCIceCandidate]) -> None:
            if candidate:
                logger.info(
                    "Discovered local ICE candidate: type=%s, protocol=%s, ip=%s, port=%s",
                    getattr(candidate, "type", "unknown"),
                    getattr(candidate, "protocol", "unknown"),
                    getattr(candidate, "ip", "unknown"),
                    getattr(candidate, "port", "unknown"),
                )

        @self.pc.on("icegatheringstatechange")
        def on_ice_gathering_state_change() -> None:
            state = self.pc.iceGatheringState
            logger.info("RTCPeerConnection iceGatheringState -> %s", state)

        @self.pc.on("connectionstatechange")
        def on_connection_state_change() -> None:
            state = self.pc.connectionState
            logger.info("RTCPeerConnection connectionState -> %s", state)
            if state in ("connected", "completed"):
                self._connected_event.set()
                self._log_active_candidate_pair()
            elif state in ("failed", "closed"):
                self._failed_event.set()

        @self.pc.on("iceconnectionstatechange")
        def on_ice_state_change() -> None:
            state = self.pc.iceConnectionState
            logger.info("RTCPeerConnection iceConnectionState -> %s", state)
            if state in ("connected", "completed"):
                self._connected_event.set()
                self._log_active_candidate_pair()
            elif state == "failed":
                self._failed_event.set()

    def _log_active_candidate_pair(self) -> None:
        """Identify and log active/selected ICE candidate pair and connection mode."""
        if self._logged_active_pair:
            return
        try:
            if self.pc.sctp and self.pc.sctp.transport:
                dtls_transport = self.pc.sctp.transport
                ice_transport = getattr(dtls_transport, "transport", None)
                connection = getattr(ice_transport, "_connection", None)
                nominated = getattr(connection, "_nominated", None)
                if nominated:
                    pair = nominated.get(1) or (list(nominated.values())[0] if nominated else None)
                    if pair:
                        local_cand = getattr(pair, "local_candidate", None)
                        remote_cand = getattr(pair, "remote_candidate", None)
                        local_type = getattr(local_cand, "type", "unknown")
                        remote_type = getattr(remote_cand, "type", "unknown")

                        if local_type == "relay" or remote_type == "relay":
                            conn_mode = "TURN relay"
                        elif local_type in ("srflx", "prflx") or remote_type in ("srflx", "prflx"):
                            conn_mode = "STUN server-reflexive (srflx)"
                        elif local_type == "host" and remote_type == "host":
                            conn_mode = "direct/host"
                        else:
                            conn_mode = f"{local_type} <-> {remote_type}"

                        local_host = getattr(local_cand, "host", "unknown")
                        local_port = getattr(local_cand, "port", "unknown")
                        remote_host = getattr(remote_cand, "host", "unknown")
                        remote_port = getattr(remote_cand, "port", "unknown")

                        self._logged_active_pair = True
                        logger.info(
                            "Selected ICE candidate pair: %s (%s candidate [%s:%s] <-> %s candidate [%s:%s])",
                            conn_mode,
                            local_type,
                            local_host,
                            local_port,
                            remote_type,
                            remote_host,
                            remote_port,
                        )
                        return
            logger.info("Active ICE candidate pair detail not available from transport.")
        except Exception as exc:
            logger.debug("Error retrieving active ICE candidate pair: %s", exc)

    @property
    def connection_state(self) -> str:
        """Current WebRTC connection state."""
        return self.pc.connectionState

    @property
    def ice_connection_state(self) -> str:
        """Current ICE connection state."""
        return self.pc.iceConnectionState

    @property
    def is_connected(self) -> bool:
        """Check if WebRTC or ICE state is connected/completed."""
        return (
            self.pc.connectionState in ("connected", "completed")
            or self.pc.iceConnectionState in ("connected", "completed")
        )

    async def create_offer(self) -> Dict[str, Any]:
        """Create SDP offer, set local description, and return offer signal dictionary."""
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        logger.info("SDP offer created and set as local description")
        return {"type": "offer", "sdp": self.pc.localDescription.sdp}

    async def handle_offer(self, offer_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming SDP offer, create answer, set descriptions, and return answer signal dictionary."""
        offer_sdp = offer_data.get("sdp")
        if not offer_sdp:
            raise PeerConnectionError("Missing SDP in offer payload")

        desc = RTCSessionDescription(sdp=offer_sdp, type="offer")
        await self.pc.setRemoteDescription(desc)
        logger.info("Remote SDP offer set successfully")

        # Flush any queued ICE candidates received before remote description
        await self._flush_pending_candidates()

        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        logger.info("SDP answer created and set as local description")
        return {"type": "answer", "sdp": self.pc.localDescription.sdp}

    async def handle_answer(self, answer_data: Dict[str, Any]) -> None:
        """Handle incoming SDP answer and set remote description."""
        answer_sdp = answer_data.get("sdp")
        if not answer_sdp:
            raise PeerConnectionError("Missing SDP in answer payload")

        desc = RTCSessionDescription(sdp=answer_sdp, type="answer")
        await self.pc.setRemoteDescription(desc)
        logger.info("Remote SDP answer set successfully")

        # Flush any queued ICE candidates
        await self._flush_pending_candidates()

    async def handle_candidate(self, candidate_data: Dict[str, Any]) -> None:
        """Handle incoming ICE candidate payload, applying or queueing if remote description not yet set."""
        if self.pc.remoteDescription is None:
            logger.debug("Queueing ICE candidate until remote description is set")
            self._pending_candidates.append(candidate_data)
            return
        await self._apply_candidate(candidate_data)

    async def _apply_candidate(self, candidate_data: Dict[str, Any]) -> None:
        """Parse and add a single ICE candidate to the RTCPeerConnection."""
        sdp_str = candidate_data.get("candidate")
        if not sdp_str:
            await self.pc.addIceCandidate(None)
            return

        try:
            cand = candidate_from_sdp(sdp_str)
            cand.sdpMid = candidate_data.get("sdpMid")
            cand.sdpMLineIndex = candidate_data.get("sdpMLineIndex")
            logger.info(
                "Discovered remote ICE candidate: type=%s, protocol=%s, ip=%s, port=%s",
                getattr(cand, "type", "unknown"),
                getattr(cand, "protocol", "unknown"),
                getattr(cand, "ip", "unknown"),
                getattr(cand, "port", "unknown"),
            )
            await self.pc.addIceCandidate(cand)
            logger.debug("Applied ICE candidate: %s", cand)
        except Exception as exc:
            logger.warning("Failed to apply ICE candidate: %s", exc)

    async def _flush_pending_candidates(self) -> None:
        """Apply all pending ICE candidates once remote description is set."""
        if not self._pending_candidates:
            return
        logger.info("Flushing %d pending ICE candidate(s)", len(self._pending_candidates))
        for cand_data in self._pending_candidates:
            await self._apply_candidate(cand_data)
        self._pending_candidates.clear()

    async def wait_connected(self, timeout: float = 30.0) -> None:
        """Wait until connection reaches 'connected' or 'completed' state."""
        if self.is_connected:
            return

        done, pending = await asyncio.wait(
            [
                asyncio.create_task(self._connected_event.wait()),
                asyncio.create_task(self._failed_event.wait()),
            ],
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        if not done:
            raise PeerConnectionError(f"WebRTC connection timed out after {timeout}s (state: {self.pc.connectionState})")

        if self._failed_event.is_set():
            raise PeerConnectionError(f"WebRTC connection failed (state: {self.pc.connectionState})")

    async def wait_channels_open(self, timeout: float = 15.0) -> None:
        """Wait until both control and data channels are open."""
        await self.channels.wait_channels_open(timeout=timeout)

    async def close(self) -> None:
        """Close DataChannels and RTCPeerConnection cleanly."""
        self.channels.close()
        await self.pc.close()
        logger.info("PeerConnectionWrapper closed")


async def establish_webrtc_connection(
    role: str,
    signaling_client: SignalingClient,
    settings: Optional[Settings] = None,
    timeout: float = 30.0,
) -> PeerConnectionWrapper:
    """Coordinate full WebRTC SDP offer/answer and ICE exchange through signaling client.

    Returns the established PeerConnectionWrapper with open DataChannels.
    """
    wrapper = PeerConnectionWrapper(settings=settings, role=role)

    # Relay local ICE candidate events to peer via signaling
    @wrapper.pc.on("icecandidate")
    async def on_local_candidate(candidate: Optional[RTCIceCandidate]) -> None:
        if candidate is not None:
            cand_payload = {
                "type": "candidate",
                "candidate": candidate_to_sdp(candidate),
                "sdpMid": candidate.sdpMid,
                "sdpMLineIndex": candidate.sdpMLineIndex,
            }
            try:
                await signaling_client.send_signal(cand_payload)
            except Exception as exc:
                logger.warning("Failed to send local ICE candidate: %s", exc)

    async def signaling_signal_loop() -> None:
        """Process incoming signaling signals for SDP and ICE exchange."""
        async for msg in signaling_client.messages():
            if msg.get("type") != "signal":
                continue
            payload = msg.get("payload", {})
            signal_type = payload.get("type")

            if signal_type == "offer" and role == "receive":
                answer_signal = await wrapper.handle_offer(payload)
                await signaling_client.send_signal(answer_signal)
            elif signal_type == "answer" and role == "send":
                await wrapper.handle_answer(payload)
            elif signal_type == "candidate":
                await wrapper.handle_candidate(payload)

            if wrapper.is_connected:
                break

    signal_task = asyncio.create_task(signaling_signal_loop())

    try:
        if role == "send":
            offer_signal = await wrapper.create_offer()
            await signaling_client.send_signal(offer_signal)

        # Wait until connectionState or iceConnectionState reaches connected
        await wrapper.wait_connected(timeout=timeout)
        logger.info("WebRTC connection established. Waiting for DataChannels to open...")

        # Await DataChannels open
        await wrapper.wait_channels_open(timeout=timeout)
        logger.info("DataChannels open. Reporting connected to signaling server...")
        await signaling_client.report_connected()
        return wrapper

    except Exception as exc:
        await wrapper.close()
        raise PeerConnectionError(f"WebRTC establishment failed: {exc}") from exc
    finally:
        if not signal_task.done():
            signal_task.cancel()
            try:
                await signal_task
            except asyncio.CancelledError:
                pass
