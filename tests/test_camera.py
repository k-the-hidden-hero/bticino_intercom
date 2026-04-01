"""Tests for the BTicino camera entity image URL extraction."""

from __future__ import annotations


class TestExtractImageFromEvent:
    """Test _extract_image_from_event for both WS and API event formats."""

    def _make_camera(self, image_type: str):
        """Create a camera instance without coordinator (for unit testing the extraction method)."""

        # We only need the _image_type attribute for _extract_image_from_event
        class FakeCamera:
            _image_type = image_type

        fake = FakeCamera()
        # Bind the method from the real class
        import types

        from custom_components.bticino_intercom.camera import BticinoBaseEventCamera

        fake._extract_image_from_event = types.MethodType(BticinoBaseEventCamera._extract_image_from_event, fake)
        return fake

    def test_ws_status_event_snapshot_url(self) -> None:
        """WS Format B event with direct snapshot_url."""
        cam = self._make_camera("snapshot")
        event = {
            "type": "incoming_call",
            "timestamp": 1774877242,
            "snapshot_url": "https://example.com/snapshot.jpg",
            "vignette_url": "https://example.com/vignette.jpg",
        }
        url, expires, time = cam._extract_image_from_event(event)

        assert url == "https://example.com/snapshot.jpg"
        assert expires is None  # WS events don't include expiry
        assert time == 1774877242

    def test_ws_status_event_vignette_url(self) -> None:
        """WS Format B event with direct vignette_url."""
        cam = self._make_camera("vignette")
        event = {
            "type": "incoming_call",
            "timestamp": 1774877242,
            "snapshot_url": "https://example.com/snapshot.jpg",
            "vignette_url": "https://example.com/vignette.jpg",
        }
        url, expires, time = cam._extract_image_from_event(event)

        assert url == "https://example.com/vignette.jpg"
        assert time == 1774877242

    def test_api_history_event_with_subevents(self) -> None:
        """API history event with nested subevents[0].snapshot.url."""
        cam = self._make_camera("snapshot")
        event = {
            "type": "outdoor",
            "time": 1774877200,
            "subevents": [
                {
                    "type": "incoming_call",
                    "time": 1774877242,
                    "snapshot": {
                        "url": "https://blob.example.com/snapshot_from_api.jpg",
                        "expires_at": 1774880842,
                    },
                    "vignette": {
                        "url": "https://blob.example.com/vignette_from_api.jpg",
                        "expires_at": 1774880842,
                    },
                }
            ],
        }
        url, expires, time = cam._extract_image_from_event(event)

        assert url == "https://blob.example.com/snapshot_from_api.jpg"
        assert expires == 1774880842
        assert time == 1774877242

    def test_api_history_event_vignette(self) -> None:
        """API history event — extracting vignette instead of snapshot."""
        cam = self._make_camera("vignette")
        event = {
            "type": "outdoor",
            "time": 1774877200,
            "subevents": [
                {
                    "type": "incoming_call",
                    "time": 1774877242,
                    "snapshot": {"url": "https://example.com/snap.jpg", "expires_at": 100},
                    "vignette": {"url": "https://example.com/vig.jpg", "expires_at": 200},
                }
            ],
        }
        url, expires, time = cam._extract_image_from_event(event)

        assert url == "https://example.com/vig.jpg"
        assert expires == 200

    def test_event_with_no_image_data(self) -> None:
        """Event without any image data returns None."""
        cam = self._make_camera("snapshot")
        event = {
            "type": "connection",
            "time": 1774877000,
        }
        url, expires, time = cam._extract_image_from_event(event)

        assert url is None
        assert expires is None
        assert time == 1774877000

    def test_ws_snapshot_url_takes_priority_over_subevents(self) -> None:
        """If both direct URL and subevents exist, direct URL wins."""
        cam = self._make_camera("snapshot")
        event = {
            "timestamp": 1774877242,
            "snapshot_url": "https://example.com/direct.jpg",
            "subevents": [
                {
                    "snapshot": {"url": "https://example.com/nested.jpg", "expires_at": 100},
                }
            ],
        }
        url, expires, time = cam._extract_image_from_event(event)

        assert url == "https://example.com/direct.jpg"
