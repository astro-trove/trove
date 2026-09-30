"""
Tests for the Ninja API authentication classes in trove_tom/auth.py.
"""
import pytest
from unittest.mock import MagicMock, patch
from django.test import RequestFactory
from rest_framework.authtoken.models import Token

from trove_tom.auth import TokenAuth


class TestTokenAuth:
    """Tests for TokenAuth (Authorization: Bearer <key>)."""

    def _call(self, header=None, user=None):
        headers = {"HTTP_AUTHORIZATION": header} if header else {}
        request = RequestFactory().get("/api/score/S251112cm", **headers)
        with patch.object(Token.objects, "select_related") as mock_sr:
            if user is None:
                mock_sr.return_value.get.side_effect = Token.DoesNotExist
            else:
                mock_sr.return_value.get.return_value = MagicMock(user=user)
            return TokenAuth()(request), mock_sr

    def test_valid_token_returns_user(self):
        user = MagicMock(is_active=True)
        result, mock_sr = self._call("Bearer abc123", user=user)
        assert result is user
        mock_sr.return_value.get.assert_called_once_with(key="abc123")

    def test_unknown_token_rejected(self):
        result, _ = self._call("Bearer notarealkey")
        assert result is None

    def test_inactive_user_rejected(self):
        result, _ = self._call("Bearer abc123", user=MagicMock(is_active=False))
        assert result is None

    def test_drf_token_prefix_rejected(self):
        result, mock_sr = self._call("Token abc123", user=MagicMock(is_active=True))
        assert result is None
        mock_sr.assert_not_called()

    def test_missing_header_rejected(self):
        result, _ = self._call()
        assert result is None
