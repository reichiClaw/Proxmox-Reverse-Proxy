from __future__ import annotations

import pytest

from gui.app import auth


class TestBootstrap:
    def test_creates_account_and_authenticates(self):
        auth.bootstrap_admin("operator", "long-enough-password")
        assert not auth.is_bootstrap_required()
        assert auth.authenticate("operator", "long-enough-password")
        assert not auth.authenticate("operator", "wrong-password-here")
        assert not auth.authenticate("other", "long-enough-password")

    def test_rejects_short_password(self):
        with pytest.raises(ValueError):
            auth.bootstrap_admin("admin", "short")

    @pytest.mark.parametrize("bad", ["a b", "user\nname", "x" * 33, "#admin"])
    def test_rejects_bad_usernames(self, bad):
        with pytest.raises(ValueError):
            auth.bootstrap_admin(bad, "long-enough-password")

    def test_empty_username_defaults_to_admin(self):
        auth.bootstrap_admin("", "long-enough-password")
        assert auth.authenticate("admin", "long-enough-password")


class TestAuthenticateWithoutAccount:
    def test_always_fails_before_bootstrap(self):
        assert auth.is_bootstrap_required()
        assert not auth.authenticate("admin", "")
        assert not auth.authenticate("admin", "anything")


class TestThrottle:
    def test_locks_after_max_failures(self):
        throttle = auth.LoginThrottle(max_failures=3, window=60, lockout=60)
        for _ in range(3):
            assert not throttle.is_locked("k")
            throttle.record_failure("k")
        assert throttle.is_locked("k")

    def test_success_resets(self):
        throttle = auth.LoginThrottle(max_failures=3, window=60, lockout=60)
        throttle.record_failure("k")
        throttle.record_failure("k")
        throttle.record_success("k")
        throttle.record_failure("k")
        assert not throttle.is_locked("k")

    def test_multiple_keys_independent(self):
        throttle = auth.LoginThrottle(max_failures=2, window=60, lockout=60)
        throttle.record_failure("a", "b")
        throttle.record_failure("a")
        assert throttle.is_locked("a")
        assert not throttle.is_locked("b")
