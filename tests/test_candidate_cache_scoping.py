"""
The scored candidate list is cached, and the list is filtered by what the
requesting user may view. Before the key carried the viewer, whoever loaded a
page first decided what everyone saw for the next five minutes.
"""

import pytest
from unittest.mock import MagicMock


class TestScoredCandidatesCacheKey:
    """The key has to record who the cached list was computed for."""

    def _key(self, user):
        from django.http import QueryDict
        from trove_nonlocalizedevents.views import scored_candidates_cache_key

        return scored_candidates_cache_key(
            QueryDict("nonlocalizedevent=16999"), True, "trove", user
        )

    def test_anonymous_and_logged_in_get_different_keys(self):
        """The reported bug: an anonymous visit left logged-in users with an
        empty list, and a logged-in visit leaked that list to anonymous ones."""
        anon = MagicMock(pk=None)
        user = MagicMock(pk=7)
        assert self._key(anon) != self._key(user)

    def test_two_users_get_different_keys(self):
        assert self._key(MagicMock(pk=7)) != self._key(MagicMock(pk=8))

    def test_anonymous_key_is_stable(self):
        assert self._key(MagicMock(pk=None)) == self._key(MagicMock(pk=None))

    def test_same_user_shares_one_key(self):
        assert self._key(MagicMock(pk=7)) == self._key(MagicMock(pk=7))

    def test_page_number_still_shares_one_scored_list(self):
        from django.http import QueryDict
        from trove_nonlocalizedevents.views import scored_candidates_cache_key

        user = MagicMock(pk=7)
        a = scored_candidates_cache_key(
            QueryDict("nonlocalizedevent=16999&page=1"), True, "trove", user)
        b = scored_candidates_cache_key(
            QueryDict("nonlocalizedevent=16999&page=4"), True, "trove", user)
        assert a == b

    def test_toggles_still_separate_the_key(self):
        from django.http import QueryDict
        from trove_nonlocalizedevents.views import scored_candidates_cache_key

        user = MagicMock(pk=7)
        q = QueryDict("nonlocalizedevent=16999")
        assert (scored_candidates_cache_key(q, True, "trove", user)
                != scored_candidates_cache_key(q, False, "trove", user))
        assert (scored_candidates_cache_key(q, True, "trove", user)
                != scored_candidates_cache_key(q, True, "kilonova", user))


class TestPerViewerToggles:
    """Both toggles moved out of the global cache and into the session."""

    def _request(self, session=None):
        request = MagicMock()
        request.session = {} if session is None else session
        return request

    def test_phot_method_defaults_without_a_request(self):
        """Background workers have no session; they must get the default
        rather than whatever the last viewer happened to pick."""
        from scoring.phot_method import get_phot_method, PHOT_METHOD_DEFAULT

        assert get_phot_method() == PHOT_METHOD_DEFAULT
        assert get_phot_method(None) == PHOT_METHOD_DEFAULT

    def test_phot_method_round_trips_through_the_session(self):
        from scoring.phot_method import (
            get_phot_method, set_phot_method, PHOT_METHOD_KILONOVA)

        request = self._request()
        set_phot_method(request, PHOT_METHOD_KILONOVA)
        assert request.session["phot_method"] == PHOT_METHOD_KILONOVA
        assert get_phot_method(request) == PHOT_METHOD_KILONOVA

    def test_phot_method_toggles_independently_per_session(self):
        from scoring.phot_method import (
            get_phot_method, toggle_phot_method, PHOT_METHOD_DEFAULT)

        a, b = self._request(), self._request()
        toggle_phot_method(a)
        assert get_phot_method(a) != PHOT_METHOD_DEFAULT
        assert get_phot_method(b) == PHOT_METHOD_DEFAULT

    def test_unknown_stored_phot_method_falls_back(self):
        from scoring.phot_method import get_phot_method, PHOT_METHOD_DEFAULT

        assert get_phot_method(self._request({"phot_method": "nonsense"})) == (
            PHOT_METHOD_DEFAULT)

    def test_agn_toggle_defaults_on_and_without_a_request(self):
        from scoring.view_prefs import get_agn_toggle

        assert get_agn_toggle() is True
        assert get_agn_toggle(self._request()) is True

    def test_agn_toggle_is_per_session(self):
        from scoring.view_prefs import get_agn_toggle, set_agn_toggle

        a, b = self._request(), self._request()
        set_agn_toggle(a, False)
        assert get_agn_toggle(a) is False
        assert get_agn_toggle(b) is True
