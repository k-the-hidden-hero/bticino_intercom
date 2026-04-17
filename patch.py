import re

with open("/tmp/bticino/custom_components/bticino_intercom/coordinator.py", "r") as f:
    content = f.read()

content = content.replace(
    'self._ws_stale = False\n',
    'self._ws_stale = False\n        self._last_incoming_call_time: dict[str, datetime] = {}\n'
)

original_call_block = """            if rtc_event_type == "call":
                new_event_type = EVENT_TYPE_INCOMING_CALL
                log_message = f"Incoming call detected via RTC for module {calling_module_id}"
                # Dispatch signal for binary sensor
                async_dispatcher_send(self.hass, SIGNAL_CALL_RECEIVED, True, calling_module_id)
                # Fire Logbook event
                self.hass.bus.async_fire(
                    EVENT_LOGBOOK_INCOMING_CALL,
                    {
                        "name": f"Incoming Call ({calling_module_name})",
                        "module_id": calling_module_id,
                    },
                )"""

new_call_block = """            if rtc_event_type == "call":
                now = datetime.now(UTC)
                last_call = getattr(self, "_last_incoming_call_time", {}).get(calling_module_id)
                if not hasattr(self, "_last_incoming_call_time"):
                    self._last_incoming_call_time = {}
                if not last_call or (now - last_call).total_seconds() > 30:
                    self._last_incoming_call_time[calling_module_id] = now
                    new_event_type = EVENT_TYPE_INCOMING_CALL
                    log_message = f"Incoming call detected via RTC for module {calling_module_id}"
                    # Dispatch signal for binary sensor
                    async_dispatcher_send(self.hass, SIGNAL_CALL_RECEIVED, True, calling_module_id)
                    # Fire Logbook event
                    self.hass.bus.async_fire(
                        EVENT_LOGBOOK_INCOMING_CALL,
                        {
                            "name": f"Incoming Call ({calling_module_name})",
                            "module_id": calling_module_id,
                        },
                    )"""

content = content.replace(original_call_block, new_call_block)

with open("/tmp/bticino/custom_components/bticino_intercom/coordinator.py", "w") as f:
    f.write(content)
