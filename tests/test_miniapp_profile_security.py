# -*- coding: utf-8 -*-
"""Regression tests for miniapp avatar validation and public-read boundaries."""

from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image

from api.middlewares.auth import _path_exempt
from api.v1.endpoints.miniapp_auth import get_public_avatar, router
from src.services.wechat_miniapp_auth_service import (
    MAX_AVATAR_BYTES,
    MiniappAuthError,
    WechatMiniappAuthService,
)


class MiniappProfileSecurityTestCase(unittest.TestCase):
    @staticmethod
    def _image_bytes(image_format: str, size=(32, 24), mode="RGB") -> bytes:
        output = BytesIO()
        Image.new(mode, size, 127).save(output, format=image_format)
        return output.getvalue()

    def test_supported_avatars_are_decoded_and_reencoded(self) -> None:
        extensions = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
        trailer = b"untrusted-avatar-trailer"

        for image_format, expected_extension in extensions.items():
            with self.subTest(image_format=image_format):
                content = self._image_bytes(image_format) + trailer
                extension, normalized = WechatMiniappAuthService._normalize_avatar(content)

                self.assertEqual(extension, expected_extension)
                self.assertNotIn(trailer, normalized)
                with Image.open(BytesIO(normalized)) as checked:
                    checked.load()
                    self.assertEqual(checked.size, (32, 24))
                    self.assertEqual(checked.format, image_format)

    def test_invalid_or_oversized_avatars_are_rejected(self) -> None:
        invalid_payloads = (
            b"\xff\xd8\xffnot-an-image",
            b"\x89PNG\r\n\x1a\n",
            b"RIFF\x04\x00\x00\x00WEBP",
            b"0" * (MAX_AVATAR_BYTES + 1),
        )
        for payload in invalid_payloads:
            with self.subTest(prefix=payload[:12]):
                with self.assertRaises(MiniappAuthError):
                    WechatMiniappAuthService._normalize_avatar(payload)

        too_wide = self._image_bytes("PNG", size=(4097, 1), mode="1")
        too_many_pixels = self._image_bytes("PNG", size=(4000, 4001), mode="1")
        for payload in (too_wide, too_many_pixels):
            with self.assertRaisesRegex(MiniappAuthError, "尺寸"):
                WechatMiniappAuthService._normalize_avatar(payload)

    def test_animated_avatar_is_rejected(self) -> None:
        output = BytesIO()
        first = Image.new("RGB", (16, 16), "red")
        second = Image.new("RGB", (16, 16), "blue")
        first.save(
            output,
            format="WEBP",
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )

        with self.assertRaisesRegex(MiniappAuthError, "单帧"):
            WechatMiniappAuthService._normalize_avatar(output.getvalue())

    def test_public_avatar_exemption_is_exact_and_read_only(self) -> None:
        filename = f"{'A' * 24}.jpg"
        path = f"/api/v1/miniapp/auth/public/avatars/{filename}"

        self.assertTrue(_path_exempt(path, "GET"))
        self.assertTrue(_path_exempt(path, "HEAD"))
        self.assertFalse(_path_exempt(path, "POST"))
        self.assertFalse(_path_exempt(f"{path}/metadata", "GET"))
        self.assertFalse(
            _path_exempt("/api/v1/miniapp/auth/public/avatars/bad.jpg", "GET")
        )
        self.assertFalse(
            _path_exempt(
                f"/api/v1/miniapp/auth/public/avatars/{'A' * 24}.svg",
                "GET",
            )
        )

    def test_public_avatar_route_and_response_support_safe_reads(self) -> None:
        route = next(
            item
            for item in router.routes
            if item.path == "/public/avatars/{filename}"
        )
        self.assertEqual(route.methods, {"GET", "HEAD"})

        class FakeService:
            @staticmethod
            def avatar_path(filename: str):
                return Path("/tmp/avatar.jpg")

        with patch(
            "api.v1.endpoints.miniapp_auth.WechatMiniappAuthService",
            return_value=FakeService(),
        ):
            response = get_public_avatar(f"{'A' * 24}.jpg")

        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(
            response.headers["cache-control"],
            "public, max-age=31536000, immutable",
        )


if __name__ == "__main__":
    unittest.main()
