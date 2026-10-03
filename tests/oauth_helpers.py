"""External OAuth server doubles shared by focused protocol tests."""

from typing import Any

from mmrelay.matrix.oauth import (
    API_SCOPE,
    DEVICE_GRANT,
    DEVICE_SCOPE,
    JsonResponse,
    OAuthSession,
)


def metadata() -> dict[str, Any]:
    return {
        "issuer": "https://auth.example.com/",
        "token_endpoint": "https://auth.example.com/token",
        "registration_endpoint": "https://auth.example.com/register",
        "device_authorization_endpoint": "https://auth.example.com/device",
        "revocation_endpoint": "https://auth.example.com/revoke",
        "grant_types_supported": [DEVICE_GRANT, "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


def session(**changes: Any) -> OAuthSession:
    values: dict[str, Any] = {
        "homeserver": "https://matrix.example.com",
        "user_id": "@bot:example.com",
        "device_id": "MMRELAY-1234567890",
        "issuer": "https://auth.example.com/",
        "token_endpoint": "https://auth.example.com/token",
        "revocation_endpoint": "https://auth.example.com/revoke",
        "client_id": "mmrelay-client",
        "access_token": "test-access-secret",
        "refresh_token": "test-refresh-secret",
        "expires_at": 1300.0,
        "refresh_at": 1270.0,
        "scope": API_SCOPE + " " + DEVICE_SCOPE + "MMRELAY-1234567890",
    }
    values.update(changes)
    return OAuthSession(**values)


class FakeServer:
    """Record outgoing requests and emulate the device-bound identity."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.polls: list[JsonResponse | Exception] = []
        self.scope = ""
        self.clock = 1000.0
        self.sleeps: list[float] = []
        self.identity_user = "@bot:example.com"
        self.identity_device: str | None = None
        self.metadata = metadata()
        self.delegation: JsonResponse = JsonResponse(404, {})
        self.stable_status = 200

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.clock += seconds

    async def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
        self.requests.append((method, url, kwargs))
        if url.endswith("/.well-known/matrix/client"):
            return self.delegation
        if url.endswith("/v1/auth_metadata"):
            return JsonResponse(self.stable_status, self.metadata)
        if url.endswith("/auth_metadata"):
            return JsonResponse(200, self.metadata)
        if url.endswith("/register"):
            return JsonResponse(201, {"client_id": "mmrelay-client"})
        if url.endswith("/device"):
            self.scope = kwargs["form"]["scope"]
            return JsonResponse(
                200,
                {
                    "device_code": "test-device-secret",
                    "user_code": "TEST-CODE",
                    "verification_uri": "https://auth.example.com/verify",
                    "expires_in": 300,
                    "interval": 5,
                },
            )
        if url.endswith("/token"):
            if self.polls:
                result = self.polls.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
            return JsonResponse(
                200,
                {
                    "access_token": "test-renewed-access",
                    "refresh_token": "test-renewed-refresh",
                    "token_type": "Bearer",
                    "expires_in": 300,
                },
            )
        if url.endswith("/whoami"):
            device_id = self.scope.split(DEVICE_SCOPE)[1]
            return JsonResponse(
                200,
                {
                    "user_id": self.identity_user,
                    "device_id": self.identity_device or device_id,
                },
            )
        if url.endswith("/revoke"):
            return JsonResponse(200, {})
        raise AssertionError(f"Unexpected request: {method} {url}")
