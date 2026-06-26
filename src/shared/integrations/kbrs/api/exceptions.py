from __future__ import annotations


class ToucanApiError(RuntimeError):
    """Base error for Toucan API/client failures."""


class ToucanTransportError(ToucanApiError):
    """HTTP/network level failure."""


class ToucanProtocolError(ToucanApiError):
    """Invalid RPC/TLV protocol response."""


class ToucanDecodeError(ToucanApiError):
    """Binary dataset/measurement decoding failure."""


class ToucanAuthenticationError(ToucanApiError):
    """Login/session creation failure."""


class ToucanNotFoundError(ToucanApiError):
    """Requested owner/device/measurement was not found."""
