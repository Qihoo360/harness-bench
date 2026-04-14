import pytest
from config_manager import deep_update

def test_shallow_update():
    base = {"app_name": "OpenClaw", "version": "1.0"}
    update = {"version": "1.1", "debug": True}
    expected = {"app_name": "OpenClaw", "version": "1.1", "debug": True}
    assert deep_update(base, update) == expected

def test_nested_deep_update():
    base = {
        "db": {"host": "localhost", "port": 5432},
        "env": "dev"
    }
    update = {
        "db": {"port": 5433}, # 期望只更新 port，保留 host
        "env": "prod"
    }
    expected = {
        "db": {"host": "localhost", "port": 5433},
        "env": "prod"
    }
    # 这个断言会失败，因为现有的代码会把 db 整个替换成 {"port": 5433}
    assert deep_update(base, update) == expected