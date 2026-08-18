from io import BytesIO
import unittest

from deskhelm_bridge.event import ProtocolError
from deskhelm_bridge.transport import (
    AGENT_EVENT_V1_CAPABILITY,
    INTERACTION_EVENT_V1_CAPABILITY,
    INTERACTION_SUBSCRIPTION_V1_CAPABILITY,
    MAX_FRAME_BYTES,
    STATE_SUBSCRIPTION_V1_CAPABILITY,
    ClientHello,
    ClientRole,
    ProtocolErrorFrame,
    ServerHello,
    decode_json_object,
    encode_frame,
    read_frame,
)


class TransportProtocolTests(unittest.TestCase):
    def test_client_hello_round_trip(self) -> None:
        value = {
            "protocol_version": 1,
            "message_type": "client_hello",
            "client_id": "codex-hook-1",
            "role": "publisher",
            "supported_versions": [1],
            "capabilities": [AGENT_EVENT_V1_CAPABILITY],
        }

        hello = ClientHello.from_dict(value)

        self.assertEqual(hello.to_dict(), value)
        self.assertEqual(hello.role, ClientRole.PUBLISHER)

    def test_client_hello_rejects_duplicate_versions_and_capabilities(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "supported_versions"):
            ClientHello(
                client_id="duplicate-version",
                role=ClientRole.PUBLISHER,
                supported_versions=(1, 1),
                capabilities=(AGENT_EVENT_V1_CAPABILITY,),
            )

        with self.assertRaisesRegex(ProtocolError, "capabilities"):
            ClientHello(
                client_id="duplicate-capability",
                role=ClientRole.PUBLISHER,
                supported_versions=(1,),
                capabilities=(
                    AGENT_EVENT_V1_CAPABILITY,
                    AGENT_EVENT_V1_CAPABILITY,
                ),
            )

    def test_server_hello_exposes_process_limits(self) -> None:
        hello = ServerHello(
            selected_version=1,
            accepted_capabilities=(AGENT_EVENT_V1_CAPABILITY,),
            stream_id="stream-1",
            max_frame_bytes=MAX_FRAME_BYTES,
            max_connections=8,
            max_subscribers=4,
            subscriber_queue_frames=8,
        )

        self.assertEqual(
            hello.to_dict()["limits"],
            {
                "max_frame_bytes": MAX_FRAME_BYTES,
                "max_connections": 8,
                "max_subscribers": 4,
                "subscriber_queue_frames": 8,
            },
        )
        self.assertEqual(ServerHello.from_dict(hello.to_dict()), hello)

    def test_capabilities_have_stable_wire_names(self) -> None:
        self.assertEqual(STATE_SUBSCRIPTION_V1_CAPABILITY, "state_subscription_v1")
        self.assertEqual(INTERACTION_EVENT_V1_CAPABILITY, "interaction_event_v1")
        self.assertEqual(
            INTERACTION_SUBSCRIPTION_V1_CAPABILITY,
            "interaction_subscription_v1",
        )

    def test_server_hello_parses_earlier_v1_without_subscriber_limits(self) -> None:
        value = {
            "protocol_version": 1,
            "message_type": "server_hello",
            "selected_version": 1,
            "accepted_capabilities": [AGENT_EVENT_V1_CAPABILITY],
            "stream_id": "stream-legacy-v1",
            "limits": {
                "max_frame_bytes": MAX_FRAME_BYTES,
                "max_connections": 8,
            },
        }

        hello = ServerHello.from_dict(value)

        self.assertIsNone(hello.max_subscribers)
        self.assertEqual(hello.to_dict(), value)

    def test_frame_encoding_is_utf8_ndjson_and_bounded(self) -> None:
        encoded = encode_frame({"message": "你好"})

        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual(decode_json_object(encoded[:-1]), {"message": "你好"})

        with self.assertRaisesRegex(ProtocolError, "maximum size"):
            encode_frame({"message": "x" * MAX_FRAME_BYTES})

    def test_protocol_error_frame_is_self_describing(self) -> None:
        error = ProtocolErrorFrame("invalid_frame", "bad payload")
        value = error.to_dict()

        self.assertEqual(value["message_type"], "protocol_error")
        self.assertEqual(value["code"], "invalid_frame")
        self.assertEqual(ProtocolErrorFrame.from_dict(value), error)

    def test_frame_reader_accepts_limit_and_rejects_oversize(self) -> None:
        frame = b"x" * MAX_FRAME_BYTES

        self.assertEqual(read_frame(BytesIO(frame + b"\n")), frame)
        with self.assertRaisesRegex(ProtocolError, "maximum size"):
            read_frame(BytesIO(frame + b"x\n"))


if __name__ == "__main__":
    unittest.main()
