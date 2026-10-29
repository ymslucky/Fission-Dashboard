# -*- coding: utf-8 -*-
"""测试桩：monkeypatch k8s.request_k8s 等网络出口。"""
import pytest

from server.main import create_app


@pytest.fixture
def app():
    return create_app()
