"""配置解析相关单元测试。"""

from __future__ import annotations

import pytest

from local_document_search import config as config_module
from local_document_search.config import TailwindAssetMode
from local_document_search.exceptions import ConfigurationError


def test_load_app_config_defaults_tailwind_asset_mode_to_cdn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证未显式配置时，Tailwind 资源来源默认使用 CDN。"""

    monkeypatch.delenv("TAILWIND_ASSET_MODE", raising=False)
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: None)

    config = config_module.load_app_config(force_reload=True)

    assert config.tailwind_asset_mode is TailwindAssetMode.CDN


def test_load_app_config_accepts_local_tailwind_asset_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证可以显式切换到本地 Tailwind 资源模式。"""

    monkeypatch.setenv("TAILWIND_ASSET_MODE", "local")
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: None)

    config = config_module.load_app_config(force_reload=True)

    assert config.tailwind_asset_mode is TailwindAssetMode.LOCAL


def test_load_app_config_rejects_invalid_tailwind_asset_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证非法的 Tailwind 资源模式会抛出配置错误。"""

    monkeypatch.setenv("TAILWIND_ASSET_MODE", "offline")
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: None)

    with pytest.raises(ConfigurationError, match="TAILWIND_ASSET_MODE"):
        config_module.load_app_config(force_reload=True)
