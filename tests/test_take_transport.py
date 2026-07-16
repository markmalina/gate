"""TAKE transport remains a dormant, media-blind Gate boundary."""

import unittest
from dataclasses import fields
from datetime import UTC, datetime

from app.application.take_transport import (
    TakeAssetReference,
    TakeCommandKind,
    TakeTransportCommand,
    build_approval_command,
    build_create_project_command,
)


def asset(workspace: str = "take-workspace") -> TakeAssetReference:
    return TakeAssetReference(
        asset_id="asset-1",
        storage_key=f"{workspace}/projects/new/source.mp4",
        sha256="a" * 64,
        size_bytes=100,
        mime_type="video/mp4",
    )


class TakeTransportTests(unittest.TestCase):
    def test_create_project_contains_reference_not_media_logic(self) -> None:
        command = build_create_project_command(
            take_workspace_id="take-workspace",
            actor_ref="opaque-actor",
            source_event_ref="opaque-update",
            title="Morning Reel",
            asset=asset(),
            occurred_at=datetime(2026, 7, 17, tzinfo=UTC),
        )
        wire = command.to_wire()
        self.assertEqual(wire["kind"], "CREATE_PROJECT")
        self.assertTrue(wire["payload"]["asset"]["storage_key"].startswith("take-workspace/"))
        field_names = {item.name for item in fields(TakeTransportCommand)}
        self.assertFalse({"transcript", "edit_plan", "subtitles", "ffmpeg"} & field_names)

    def test_cross_workspace_asset_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "workspace"):
            build_create_project_command(
                take_workspace_id="take-workspace",
                actor_ref="opaque-actor",
                source_event_ref="opaque-update",
                title="Morning Reel",
                asset=asset("another-workspace"),
                occurred_at=datetime(2026, 7, 17, tzinfo=UTC),
            )

    def test_approval_requires_exact_confirmation_and_hash(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit confirmation"):
            build_approval_command(
                take_workspace_id="take-workspace",
                actor_ref="opaque-actor",
                source_event_ref="opaque-update",
                project_id="p1",
                version_id="v2",
                video_sha256="b" * 64,
                confirmation=False,
                occurred_at=datetime(2026, 7, 17, tzinfo=UTC),
            )

    def test_repr_redacts_actor_source_and_storage_reference(self) -> None:
        command = build_create_project_command(
            take_workspace_id="take-workspace",
            actor_ref="secret-actor-ref",
            source_event_ref="secret-source-ref",
            title="Morning Reel",
            asset=asset(),
            occurred_at=datetime(2026, 7, 17, tzinfo=UTC),
        )
        rendered = repr(command)
        self.assertNotIn("secret-actor-ref", rendered)
        self.assertNotIn("secret-source-ref", rendered)
        self.assertNotIn("source.mp4", repr(asset()))

    def test_non_create_command_requires_project(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires project_id"):
            TakeTransportCommand(
                command_id="00000000-0000-0000-0000-000000000001",
                take_workspace_id="take-workspace",
                actor_ref="opaque-actor",
                source_event_ref="opaque-source",
                project_id=None,
                kind=TakeCommandKind.REQUEST_STATUS,
                idempotency_key="a" * 64,
                payload={},
                occurred_at=datetime(2026, 7, 17, tzinfo=UTC),
            )


if __name__ == "__main__":
    unittest.main()
