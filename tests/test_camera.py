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


class TestInjectAudioSsrc:
    """Test audio SSRC injection into SDP offer."""

    def test_ssrc_added_to_audio_section(self) -> None:
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        sdp = (
            "v=0\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=mid:0\r\n"
            "a=recvonly\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=mid:1\r\n"
            "a=recvonly\r\n"
        )
        result = BticinoWebRTCCamera._inject_audio_ssrc(sdp)
        lines = result.split("\r\n")
        audio_idx = next(i for i, line in enumerate(lines) if line.startswith("m=audio"))
        video_idx = next(i for i, line in enumerate(lines) if line.startswith("m=video"))
        audio_section = "\r\n".join(lines[audio_idx:video_idx])
        assert "a=ssrc:1000 cname:bticino-ha" in audio_section
        assert "a=ssrc:1000 msid:bticino-intercom audio0" in audio_section

    def test_ssrc_not_added_to_video(self) -> None:
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        sdp = (
            "v=0\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=mid:0\r\n"
            "a=recvonly\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=mid:1\r\n"
            "a=recvonly\r\n"
        )
        result = BticinoWebRTCCamera._inject_audio_ssrc(sdp)
        lines = result.split("\r\n")
        video_idx = next(i for i, line in enumerate(lines) if line.startswith("m=video"))
        video_section = "\r\n".join(lines[video_idx:])
        assert "cname:bticino-ha" not in video_section

    def test_no_duplicate_if_ssrc_exists(self) -> None:
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        sdp = (
            "v=0\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=mid:0\r\n"
            "a=sendrecv\r\n"
            "a=ssrc:99999 cname:existing\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        )
        result = BticinoWebRTCCamera._inject_audio_ssrc(sdp)
        assert result.count("a=ssrc:") == 1

    def test_audio_last_section(self) -> None:
        """SSRC injected even if audio is the last m-section."""
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=mid:0\r\na=recvonly\r\n"
        result = BticinoWebRTCCamera._inject_audio_ssrc(sdp)
        assert "a=ssrc:1000 cname:bticino-ha" in result


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


class TestReorderMlines:
    """Tests for BticinoWebRTCCamera._reorder_mlines static method."""

    def test_matching_order_unchanged(self) -> None:
        """When order already matches, SDP is returned as-is."""
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        offer = "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=mid:1\r\n"
        answer = (
            "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=sendonly\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=sendrecv\r\n"
        )
        result = BticinoWebRTCCamera._reorder_mlines(answer, offer)
        assert result == answer

    def test_mismatched_order_reordered(self) -> None:
        """When offer is (audio, video) but answer is (video, audio), reorder."""
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        offer = (
            "v=0\r\n"
            "o=- 1 0 IN IP4 0.0.0.0\r\n"
            "s=-\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=mid:0\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=mid:1\r\n"
            "a=rtpmap:96 H264/90000\r\n"
        )
        answer = (
            "v=0\r\n"
            "o=- 2 0 IN IP4 0.0.0.0\r\n"
            "s=-\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=sendonly\r\n"
            "a=fingerprint:sha-256 AA:BB\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=sendrecv\r\n"
            "a=fingerprint:sha-256 AA:BB\r\n"
        )
        result = BticinoWebRTCCamera._reorder_mlines(answer, offer)
        lines = result.split("\r\n")
        mlines = [line for line in lines if line.startswith("m=")]
        assert mlines[0].startswith("m=audio")
        assert mlines[1].startswith("m=video")

    def test_session_lines_preserved(self) -> None:
        """Session-level lines (before first m=) are preserved."""
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        offer = "v=0\r\ns=-\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        answer = "v=0\r\no=- 1 0 IN IP4 0.0.0.0\r\ns=-\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:1\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=mid:0\r\n"
        result = BticinoWebRTCCamera._reorder_mlines(answer, offer)
        assert result.startswith("v=0\r\no=- 1 0 IN IP4 0.0.0.0\r\ns=-\r\n")


class TestFilterVideoCodecs:
    """Test the video codec filter that keeps the offer under the ~8 KB limit.

    Above roughly 8 KB the BNC1 firmware drops its cloud connection and reboots
    instead of answering (#74), so the offer is trimmed to H264 before sending.
    """

    OFFER = (
        "v=0\r\n"
        "m=audio 9 UDP/TLS/RTP/SAVPF 111 63\r\n"
        "a=mid:0\r\n"
        "a=rtpmap:111 opus/48000/2\r\n"
        "a=rtpmap:63 red/48000/2\r\n"
        "m=video 9 UDP/TLS/RTP/SAVPF 96 97 102 103 98\r\n"
        "a=mid:1\r\n"
        "a=rtcp-fb:* transport-cc\r\n"
        "a=rtpmap:96 VP8/90000\r\n"
        "a=rtcp-fb:96 nack\r\n"
        "a=rtpmap:97 rtx/90000\r\n"
        "a=fmtp:97 apt=96\r\n"
        "a=rtpmap:102 H264/90000\r\n"
        "a=rtcp-fb:102 nack pli\r\n"
        "a=fmtp:102 level-asymmetry-allowed=1;packetization-mode=1\r\n"
        "a=rtpmap:103 rtx/90000\r\n"
        "a=fmtp:103 apt=102\r\n"
        "a=rtpmap:98 VP9/90000\r\n"
    )

    @staticmethod
    def _filter(sdp: str, **kwargs: str) -> str:
        from custom_components.bticino_intercom.camera import BticinoWebRTCCamera

        return BticinoWebRTCCamera._filter_video_codecs(sdp, **kwargs)

    def test_only_h264_payload_types_survive_in_the_m_line(self) -> None:
        result = self._filter(self.OFFER)
        m_line = next(line for line in result.split("\r\n") if line.startswith("m=video"))
        assert m_line == "m=video 9 UDP/TLS/RTP/SAVPF 102"

    def test_attributes_of_dropped_payload_types_go_with_them(self) -> None:
        result = self._filter(self.OFFER)
        for dropped in ("a=rtpmap:96", "a=rtpmap:97", "a=rtpmap:98", "a=fmtp:97", "a=rtcp-fb:96"):
            assert dropped not in result
        assert "a=rtpmap:102 H264/90000" in result
        assert "a=fmtp:102 level-asymmetry-allowed=1;packetization-mode=1" in result
        assert "a=rtcp-fb:102 nack pli" in result

    def test_wildcard_rtcp_fb_is_kept(self) -> None:
        """`a=rtcp-fb:*` applies to every payload type, so it must survive."""
        assert "a=rtcp-fb:* transport-cc" in self._filter(self.OFFER)

    def test_audio_section_is_untouched(self) -> None:
        """Audio is not what blows the size budget, and the mic depends on it."""
        result = self._filter(self.OFFER)
        lines = result.split("\r\n")
        audio = lines[
            lines.index("m=audio 9 UDP/TLS/RTP/SAVPF 111 63") : lines.index("m=video 9 UDP/TLS/RTP/SAVPF 102")
        ]
        assert audio == [
            "m=audio 9 UDP/TLS/RTP/SAVPF 111 63",
            "a=mid:0",
            "a=rtpmap:111 opus/48000/2",
            "a=rtpmap:63 red/48000/2",
        ]

    def test_payload_types_are_never_renumbered(self) -> None:
        """The safety property the whole approach rests on.

        Only removal is performed, so the device's answer is always a subset of
        what the browser offered and stays valid for setRemoteDescription with
        no reverse mapping. A renumbering filter would need one.
        """
        result = self._filter(self.OFFER)
        assert "a=rtpmap:102 H264/90000" in result
        assert result.count("a=rtpmap:") == 3  # opus, red, H264 — unchanged numbers

    def test_offer_without_the_kept_codec_is_left_alone(self) -> None:
        """Better an oversized offer than one with no video the device can use."""
        no_h264 = self.OFFER.replace("a=rtpmap:102 H264/90000", "a=rtpmap:102 AV1/90000")
        assert self._filter(no_h264) == no_h264

    def test_offer_without_a_video_section_is_left_alone(self) -> None:
        audio_only = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\n"
        assert self._filter(audio_only) == audio_only

    def test_a_full_chrome_offer_lands_under_the_limit(self) -> None:
        """The regression that matters: 43 payload types must come out under 8 KB."""
        padding = "".join(
            f"a=rtpmap:{pt} Codec{pt}/90000\r\n"
            f"a=rtcp-fb:{pt} goog-remb\r\na=rtcp-fb:{pt} transport-cc\r\n"
            f"a=rtcp-fb:{pt} ccm fir\r\na=rtcp-fb:{pt} nack\r\na=rtcp-fb:{pt} nack pli\r\n"
            f"a=fmtp:{pt} level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42001f\r\n"
            for pt in range(104, 147)
        )
        pts = " ".join(str(pt) for pt in range(104, 147))
        bloated = self.OFFER.replace(
            "m=video 9 UDP/TLS/RTP/SAVPF 96 97 102 103 98\r\n",
            f"m=video 9 UDP/TLS/RTP/SAVPF 96 97 102 103 98 {pts}\r\n",
        ).replace("a=rtpmap:98 VP9/90000\r\n", "a=rtpmap:98 VP9/90000\r\n" + padding)

        assert len(bloated) > 8000
        assert len(self._filter(bloated)) < 8000
