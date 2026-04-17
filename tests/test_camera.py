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
        url, _expires, _time = cam._extract_image_from_event(event)

        assert url == "https://example.com/snapshot.jpg"
        assert _expires is None  # WS events don't include expiry
        assert _time == 1774877242

    def test_ws_status_event_vignette_url(self) -> None:
        """WS Format B event with direct vignette_url."""
        cam = self._make_camera("vignette")
        event = {
            "type": "incoming_call",
            "timestamp": 1774877242,
            "snapshot_url": "https://example.com/snapshot.jpg",
            "vignette_url": "https://example.com/vignette.jpg",
        }
        url, _expires, _time = cam._extract_image_from_event(event)

        assert url == "https://example.com/vignette.jpg"
        assert _time == 1774877242

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
        url, _expires, _time = cam._extract_image_from_event(event)

        assert url == "https://blob.example.com/snapshot_from_api.jpg"
        assert _expires == 1774880842
        assert _time == 1774877242

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
        url, _expires, _time = cam._extract_image_from_event(event)

        assert url == "https://example.com/vig.jpg"
        assert _expires == 200

    def test_event_with_no_image_data(self) -> None:
        """Event without any image data returns None."""
        cam = self._make_camera("snapshot")
        event = {
            "type": "connection",
            "time": 1774877000,
        }
        url, _expires, _time = cam._extract_image_from_event(event)

        assert url is None
        assert _expires is None
        assert _time == 1774877000

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
        url, _expires, _time = cam._extract_image_from_event(event)

        assert url == "https://example.com/direct.jpg"


class TestEnableAudioSendrecv:
    """Test audio direction fix in SDP."""

    def test_audio_recvonly_becomes_sendrecv(self) -> None:
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\n"
        result = BticinoWebRTCCamera._enable_audio_sendrecv(sdp)
        # Audio should be sendrecv
        assert "m=audio" in result
        lines = result.split("\r\n")
        audio_idx = next(i for i, line in enumerate(lines) if line.startswith("m=audio"))
        video_idx = next(i for i, line in enumerate(lines) if line.startswith("m=video"))
        audio_section = lines[audio_idx:video_idx]
        assert "a=sendrecv" in audio_section
        # Video should stay recvonly
        video_section = lines[video_idx:]
        assert "a=recvonly" in video_section

    def test_video_stays_recvonly(self) -> None:
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\n"
        result = BticinoWebRTCCamera._enable_audio_sendrecv(sdp)
        assert result.count("a=sendrecv") == 1  # Only audio
        assert result.count("a=recvonly") == 1  # Only video


class TestFixAnswerAudioDirection:
    """Test answer SDP audio direction fix."""

    def test_sendrecv_becomes_sendonly_in_audio(self) -> None:
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=sendonly\r\n"
        result = BticinoWebRTCCamera._fix_answer_audio_direction(sdp)
        lines = result.split("\r\n")
        audio_idx = next(i for i, line in enumerate(lines) if line.startswith("m=audio"))
        video_idx = next(i for i, line in enumerate(lines) if line.startswith("m=video"))
        assert "a=sendonly" in lines[audio_idx:video_idx]
        assert "a=sendrecv" not in lines[audio_idx:video_idx]

    def test_video_direction_unchanged(self) -> None:
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=sendonly\r\n"
        result = BticinoWebRTCCamera._fix_answer_audio_direction(sdp)
        lines = result.split("\r\n")
        video_idx = next(i for i, line in enumerate(lines) if line.startswith("m=video"))
        video_section = lines[video_idx:]
        assert "a=sendonly" in video_section

    def test_already_sendonly_unchanged(self) -> None:
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendonly\r\n"
        result = BticinoWebRTCCamera._fix_answer_audio_direction(sdp)
        assert "a=sendonly" in result
        assert "a=sendrecv" not in result


class TestAudioSdpConditionalRewrite:
    """Test that answer rewriting is conditional on offer modification."""

    def test_recvonly_offer_gets_answer_fixed(self) -> None:
        """When offer has recvonly (standard HA player), answer sendrecv -> sendonly."""
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        offer = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\n"
        modified = BticinoWebRTCCamera._enable_audio_sendrecv(offer)
        was_rewritten = modified != offer
        assert was_rewritten is True

        answer = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"
        # Should fix because we rewrote the offer
        fixed = BticinoWebRTCCamera._fix_answer_audio_direction(answer)
        assert "a=sendonly" in fixed

    def test_sendrecv_offer_leaves_answer_alone(self) -> None:
        """When offer already has sendrecv (two-way card), answer stays sendrecv."""
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        offer = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"
        modified = BticinoWebRTCCamera._enable_audio_sendrecv(offer)
        was_rewritten = modified != offer
        assert was_rewritten is False
        # Should NOT fix the answer — two-way audio card expects sendrecv


class TestConvertOfferToAnswerSdp:
    """Tests for BticinoWebRTCCamera.convert_offer_to_answer_sdp static method."""

    def test_actpass_becomes_active(self) -> None:
        """a=setup:actpass is replaced with a=setup:active."""
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        offer = (
            "v=0\r\n"
            "o=- 123 0 IN IP4 0.0.0.0\r\n"
            "s=-\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=setup:actpass\r\n"
            "a=mid:0\r\n"
        )
        result = BticinoWebRTCCamera.convert_offer_to_answer_sdp(offer)
        assert "a=setup:active" in result
        assert "a=setup:actpass" not in result

    def test_other_attributes_preserved(self) -> None:
        """Non-setup attributes remain unchanged."""
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        offer = (
            "v=0\r\n"
            "o=- 123 0 IN IP4 0.0.0.0\r\n"
            "s=-\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=setup:actpass\r\n"
            "a=mid:0\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
            "a=ice-ufrag:abc\r\n"
        )
        result = BticinoWebRTCCamera.convert_offer_to_answer_sdp(offer)
        assert "a=mid:0" in result
        assert "a=rtpmap:111 opus/48000/2" in result
        assert "a=ice-ufrag:abc" in result
        assert "v=0" in result

    def test_multiple_media_sections_all_converted(self) -> None:
        """Multiple m= sections each with a=setup:actpass are all converted."""
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        offer = (
            "v=0\r\n"
            "o=- 123 0 IN IP4 0.0.0.0\r\n"
            "s=-\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=setup:actpass\r\n"
            "a=mid:0\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=setup:actpass\r\n"
            "a=mid:1\r\n"
        )
        result = BticinoWebRTCCamera.convert_offer_to_answer_sdp(offer)
        assert result.count("a=setup:active") == 2
        assert "a=setup:actpass" not in result
