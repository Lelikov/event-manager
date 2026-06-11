import time

import jwt
import pytest

from event_receiver.errors import UnauthorizedError
from event_receiver.security import AuthorizationJWTConfig, AuthorizationJWTVerifier


SECRET = "test-secret-key-for-unit-tests"  # noqa: S105
ALGORITHM = "HS256"
ISSUER = "test-issuer"
AUDIENCE = "test-audience"


def _make_verifier() -> AuthorizationJWTVerifier:
    return AuthorizationJWTVerifier(
        AuthorizationJWTConfig(
            verify_key=SECRET,
            algorithm=ALGORITHM,
            issuer=ISSUER,
            audience=AUDIENCE,
        ),
    )


def _encode_token(claims: dict, *, secret: str = SECRET) -> str:
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        **claims,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


class TestVerifySignature:
    def test_valid_token(self) -> None:
        verifier = _make_verifier()
        token = _encode_token({"sub": "user1"})
        claims = verifier.verify_signature(token=token)
        assert claims["sub"] == "user1"

    def test_invalid_signature(self) -> None:
        verifier = _make_verifier()
        token = _encode_token({"sub": "user1"}, secret="wrong-secret")  # noqa: S106
        with pytest.raises(UnauthorizedError, match="Invalid JWT signature"):
            verifier.verify_signature(token=token)

    def test_expired_token(self) -> None:
        verifier = _make_verifier()
        payload = {
            "sub": "user1",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        }
        token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
        with pytest.raises(UnauthorizedError, match="Invalid JWT signature"):
            verifier.verify_signature(token=token)

    def test_wrong_issuer(self) -> None:
        verifier = _make_verifier()
        payload = {
            "sub": "user1",
            "iss": "wrong-issuer",
            "aud": AUDIENCE,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
        with pytest.raises(UnauthorizedError, match="Invalid JWT signature"):
            verifier.verify_signature(token=token)


class TestVerify:
    def _verified_claims(self, claims: dict) -> dict:
        """Decode-and-verify path used by production code: verify_signature then verify."""
        verifier = _make_verifier()
        token = _encode_token(claims)
        return verifier.verify_signature(token=token)

    def test_source_claim_match(self) -> None:
        verifier = _make_verifier()
        decoded = self._verified_claims({"source": "jitsi", "type": "conference.joined", "room": "abc"})
        claims = verifier.verify(claims=decoded, event_source="jitsi", event_type="conference.joined")
        assert claims["room"] == "abc"
        assert "source" not in claims
        assert "type" not in claims

    def test_source_claim_mismatch_raises_unauthorized(self) -> None:
        verifier = _make_verifier()
        decoded = self._verified_claims({"source": "jitsi", "type": "conference.joined"})
        with pytest.raises(UnauthorizedError, match="source claim does not match"):
            verifier.verify(claims=decoded, event_source="other-source", event_type="conference.joined")

    def test_type_claim_mismatch_raises_unauthorized(self) -> None:
        verifier = _make_verifier()
        decoded = self._verified_claims({"source": "jitsi", "type": "conference.joined"})
        with pytest.raises(UnauthorizedError, match="type claim does not match"):
            verifier.verify(claims=decoded, event_source="jitsi", event_type="other.type")

    def test_no_source_claim_allows_any_source(self) -> None:
        verifier = _make_verifier()
        decoded = self._verified_claims({"room": "abc"})
        claims = verifier.verify(claims=decoded, event_source="anything", event_type="anything")
        assert claims["room"] == "abc"

    def test_pre_decoded_claims(self) -> None:
        verifier = _make_verifier()
        claims = verifier.verify(
            claims={"source": "jitsi", "type": "conf.joined", "room": "r1", "exp": 0, "iat": 0},
            event_source="jitsi",
            event_type="conf.joined",
        )
        assert claims["room"] == "r1"

    def test_verify_has_no_unverified_token_path(self) -> None:
        """verify() must not accept a raw token: signature bypass path was removed."""
        verifier = _make_verifier()
        with pytest.raises(TypeError):
            verifier.verify(token="some-token", event_source="x", event_type="y")  # type: ignore[call-arg]  # noqa: S106
