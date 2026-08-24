"""Offline RTSP/WFD negotiation fixtures; no sockets, radio, or media engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence


class WFDProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class RTSPFixtureMessage:
    start: str
    headers: dict[str, str]
    body: str

    @property
    def is_response(self) -> bool:
        return self.start.startswith("RTSP/")

    @property
    def method(self) -> str:
        return "" if self.is_response else self.start.split(maxsplit=1)[0]


@dataclass(frozen=True)
class WFDNegotiationResult:
    status: str
    detail: str
    trace: tuple[str, ...]
    cleanup: tuple[str, ...]
    video_advertised: bool
    audio_advertised: bool


def parse_rtsp_fixture(raw: str) -> RTSPFixtureMessage:
    head, separator, body = raw.partition("\r\n\r\n")
    if not separator:
        head, separator, body = raw.partition("\n\n")
    lines = head.replace("\r\n", "\n").splitlines()
    if not separator or not lines or not lines[0].strip():
        raise WFDProtocolError("RTSP fixture must contain a start line and header terminator")
    start = lines[0].strip()
    if not (start.startswith("RTSP/1.0 ") or start.endswith(" RTSP/1.0")):
        raise WFDProtocolError("unsupported RTSP start line")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            raise WFDProtocolError("malformed RTSP header")
        headers[key.strip().lower()] = value.strip()
    if "cseq" not in headers:
        raise WFDProtocolError("RTSP fixture is missing CSeq")
    length = headers.get("content-length")
    if length is not None:
        try:
            expected = int(length)
        except ValueError as exc:
            raise WFDProtocolError("RTSP content length is invalid") from exc
        if expected != len(body.encode("utf-8")):
            raise WFDProtocolError("RTSP content length does not match body")
    return RTSPFixtureMessage(start=start, headers=headers, body=body)


def _parameters(body: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in body.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key.strip().lower()] = value.strip()
    return result


def run_wfd_fixture(transcript: Sequence[str]) -> WFDNegotiationResult:
    """Drive the minimal source-side WFD handshake over pre-recorded messages."""
    phase = "m1-options"
    trace: list[str] = [phase]
    video_advertised = False
    audio_advertised = False
    detail = "sink did not finish WFD setup"
    status = "timeout"
    try:
        for raw in transcript:
            message = parse_rtsp_fixture(raw)
            if phase == "m1-options":
                if not message.is_response or message.headers["cseq"] != "1" or "200" not in message.start:
                    raise WFDProtocolError("sink did not accept M1 OPTIONS")
                phase = "m3-get-parameter"
                trace.append(phase)
            elif phase == "m3-get-parameter":
                if not message.is_response or message.headers["cseq"] != "2" or "200" not in message.start:
                    raise WFDProtocolError("sink did not answer M3 GET_PARAMETER")
                parameters = _parameters(message.body)
                ports = parameters.get("wfd_client_rtp_ports", "")
                video = parameters.get("wfd_video_formats", "")
                audio = parameters.get("wfd_audio_codecs", "")
                if "RTP/AVP/UDP;unicast" not in ports:
                    raise WFDProtocolError("sink did not advertise usable RTP/UDP ports")
                if not video or video.lower() == "none":
                    raise WFDProtocolError("sink did not advertise a compatible video format")
                video_advertised = True
                audio_advertised = bool(audio and audio.lower() != "none")
                phase = "m4-set-parameter"
                trace.append(phase)
            elif phase == "m4-set-parameter":
                if not message.is_response or message.headers["cseq"] != "3" or "200" not in message.start:
                    raise WFDProtocolError("sink rejected M4 SET_PARAMETER")
                phase = "m5-trigger-setup"
                trace.append(phase)
            elif phase == "m5-trigger-setup":
                if not message.is_response or message.headers["cseq"] != "4" or "200" not in message.start:
                    raise WFDProtocolError("sink rejected M5 trigger setup")
                phase = "setup"
                trace.append(phase)
            elif phase == "setup":
                if message.is_response or message.method != "SETUP":
                    raise WFDProtocolError("sink did not initiate SETUP")
                phase = "play"
                trace.append(phase)
            elif phase == "play":
                if message.is_response or message.method != "PLAY":
                    raise WFDProtocolError("sink did not request PLAY")
                phase = "streaming"
                trace.append(phase)
                status = "completed"
                detail = "offline WFD negotiation reached PLAY"
                break
        if status != "completed" and phase != "setup":
            detail = "sink transcript ended before SETUP/PLAY"
    except WFDProtocolError as exc:
        status = "incompatible"
        detail = str(exc)
        trace.append("error")
    finally:
        cleanup = ("media-stop", "rtsp-close", "p2p-cleanup-skipped-offline")
        trace.append("cleanup")
    return WFDNegotiationResult(status, detail, tuple(trace), cleanup, video_advertised, audio_advertised)


def result_payload(result: WFDNegotiationResult) -> dict[str, object]:
    return asdict(result)


SUCCESS_FIXTURE: tuple[str, ...] = (
    "RTSP/1.0 200 OK\r\nCSeq: 1\r\nContent-Length: 0\r\n\r\n",
    "RTSP/1.0 200 OK\r\nCSeq: 2\r\nContent-Length: 139\r\n\r\nwfd_video_formats: CEA 1920x1080p60 H264\r\nwfd_audio_codecs: AAC 00000001\r\nwfd_client_rtp_ports: RTP/AVP/UDP;unicast 19000 19001 mode=play\r\n",
    "RTSP/1.0 200 OK\r\nCSeq: 3\r\nContent-Length: 0\r\n\r\n",
    "RTSP/1.0 200 OK\r\nCSeq: 4\r\nContent-Length: 0\r\n\r\n",
    "SETUP rtsp://192.168.49.1/wfd1.0/streamid=0 RTSP/1.0\r\nCSeq: 10\r\nContent-Length: 0\r\n\r\n",
    "PLAY rtsp://192.168.49.1/wfd1.0/streamid=0 RTSP/1.0\r\nCSeq: 11\r\nContent-Length: 0\r\n\r\n",
)

INCOMPATIBLE_VIDEO_FIXTURE: tuple[str, ...] = SUCCESS_FIXTURE[:1] + (
    "RTSP/1.0 200 OK\r\nCSeq: 2\r\nContent-Length: 122\r\n\r\nwfd_video_formats: none\r\nwfd_audio_codecs: AAC 00000001\r\nwfd_client_rtp_ports: RTP/AVP/UDP;unicast 19000 19001 mode=play\r\n",
)

TIMEOUT_FIXTURE: tuple[str, ...] = SUCCESS_FIXTURE[:4]
