import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.web.api_handlers import _download_fav_media


def test_fav_media_export_has_no_hidden_item_cap():
    with tempfile.TemporaryDirectory() as tmp:
        items = [
            {"id": i, "type": 0, "images": [{"url": f"https://example/{i}", "key": 0}]}
            for i in range(1001)
        ]
        with patch("src.wechat.image_decrypt.download_and_decrypt", return_value=b"\xff\xd8\xff\xe0"), \
             patch.dict(os.environ, {"FAV_EXPORT_MAX_SECONDS": "0"}, clear=False):
            stats = _download_fav_media(items, tmp)

        assert stats["total"] == 1001
        assert stats["downloaded"] == 1001
        assert stats["skipped"] == 0
        assert stats["images"] == 1001
        assert len(list((Path(tmp) / "images").iterdir())) == 1001


def test_fav_media_export_continues_after_one_failure():
    with tempfile.TemporaryDirectory() as tmp:
        items = [
            {"id": i, "type": 0, "images": [{"url": f"https://example/{i}", "key": 0}]}
            for i in range(3)
        ]
        def download(url, key=None, timeout=15):
            if url.endswith("/1"):
                return None
            return b"\xff\xd8\xff\xe0"

        with patch("src.wechat.image_decrypt.download_and_decrypt", side_effect=download):
            stats = _download_fav_media(items, tmp)

        assert stats["total"] == 3
        assert stats["downloaded"] == 2
        assert stats["errors"] == 1
        assert stats["skipped"] == 0
