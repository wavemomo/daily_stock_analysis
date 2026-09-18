# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.config import Config
from src.md2img import (
    _markdown_to_image_m2f,
    _markdown_to_image_playwright,
    _markdown_to_image_wkhtml,
    markdown_to_image,
)
from src.share_image import BRAND_SHORT_NAME, PROJECT_DISPLAY_NAME


def _assert_brand_without_social(html: str) -> None:
    """分享图使用新品牌，且不含旧 DSA/开源/小红书信息。"""
    assert f"<strong>{BRAND_SHORT_NAME}</strong>" in html
    assert PROJECT_DISPLAY_NAME in html
    assert "DSA" not in html
    assert "ZhuLinsen/daily_stock_analysis" not in html
    assert "小红书" not in html
    assert "开源" not in html
    assert "GitHub" not in html
    assert 'class="qr-frame"' not in html


def test_wkhtml_renderer_uses_share_poster_dimensions_and_qr_template():
    with patch("imgkit.from_string", return_value=b"png") as render:
        assert _markdown_to_image_wkhtml(
            "# 大盘复盘\n\n## 结论\n\n震荡",
        ) == b"png"

    html, output = render.call_args.args
    options = render.call_args.kwargs["options"]
    assert output is False
    assert 'class="poster market"' in html
    _assert_brand_without_social(html)
    assert options["width"] == 1080
    assert options["disable-smart-width"] == ""


def test_markdown_to_file_renderer_receives_the_same_share_poster(tmp_path, monkeypatch):
    captured = {}
    resolved_m2f = str(tmp_path / "m2f.CMD")

    def fake_run(args, **_kwargs):
        captured["command"] = args[0]
        captured["html"] = Path(args[1]).read_text(encoding="utf-8")
        (tmp_path / "report.png").write_bytes(b"png")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr("src.md2img.shutil.which", lambda _name: resolved_m2f)
    monkeypatch.setattr("src.md2img.tempfile.mkdtemp", lambda: str(tmp_path))
    monkeypatch.setattr("src.md2img.subprocess.run", fake_run)
    monkeypatch.setattr("src.md2img.shutil.rmtree", lambda _path: None)

    assert _markdown_to_image_m2f(
        "# 贵州茅台 600519\n\n## 结论\n\n偏多",
    ) == b"png"
    assert captured["command"] == resolved_m2f
    assert 'class="poster stock"' in captured["html"]
    _assert_brand_without_social(captured["html"])


def test_playwright_renderer_receives_the_same_share_poster(tmp_path, monkeypatch):
    captured = {}
    resolved_playwright = str(tmp_path / "playwright.CMD")

    def fake_run(args, **_kwargs):
        captured["args"] = args
        captured["html"] = (tmp_path / "report.html").read_text(encoding="utf-8")
        Path(args[-1]).write_bytes(b"png")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr("src.md2img._resolve_playwright_command", lambda: resolved_playwright)
    monkeypatch.setattr("src.md2img.tempfile.mkdtemp", lambda: str(tmp_path))
    monkeypatch.setattr("src.md2img.subprocess.run", fake_run)
    monkeypatch.setattr("src.md2img.shutil.rmtree", lambda _path: None)

    assert _markdown_to_image_playwright(
        "# 贵州茅台 600519\n\n## 结论\n\n偏多",
    ) == b"png"
    assert captured["args"][0] == resolved_playwright
    assert captured["args"][1:6] == [
        "screenshot",
        "--browser",
        "chromium",
        "--viewport-size",
        "1080,720",
    ]
    assert "--full-page" in captured["args"]
    assert 'class="poster stock"' in captured["html"]
    _assert_brand_without_social(captured["html"])


def test_config_accepts_playwright_image_engine():
    assert Config._parse_md2img_engine("playwright") == "playwright"


def test_markdown_to_image_dispatches_without_branding_argument():
    config = SimpleNamespace(md2img_engine="wkhtmltoimage")
    with (
        patch("src.config.get_config", return_value=config),
        patch("src.md2img._markdown_to_image_wkhtml", return_value=b"png") as render,
    ):
        assert markdown_to_image("# 大盘复盘") == b"png"

    # 渲染器只接收 (markdown, structured_payload)，不再透传 branding
    assert render.call_args.args == ("# 大盘复盘", None)
    assert "branding" not in render.call_args.kwargs


def test_wkhtml_renderer_forwards_structured_analysis_payload():
    payload = {
        "name": "中钨高新",
        "code": "000657",
        "operation_advice": "观望",
        "sentiment_score": 40,
        "dashboard": {"core_conclusion": {"one_sentence": "等待企稳"}},
    }
    with patch("imgkit.from_string", return_value=b"png") as render:
        assert _markdown_to_image_wkhtml(
            "# 占位名称 000657\n\n## 核心结论\n\n占位文本",
            structured_payload=payload,
        ) == b"png"

    html = render.call_args.args[0]
    assert "中钨高新" in html
    assert "等待企稳" in html


def test_wkhtml_renderer_returns_none_when_share_poster_build_fails():
    with patch("src.md2img.build_share_image_html", side_effect=FileNotFoundError("missing qr")):
        assert _markdown_to_image_wkhtml("# 大盘复盘") is None


def test_markdown_to_file_renderer_returns_none_when_share_poster_build_fails(monkeypatch):
    monkeypatch.setattr("src.md2img.shutil.which", lambda _name: "m2f")

    with patch("src.md2img.build_share_image_html", side_effect=FileNotFoundError("missing qr")):
        assert _markdown_to_image_m2f("# 贵州茅台 600519") is None
