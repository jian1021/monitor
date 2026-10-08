# -*- coding: utf-8 -*-
"""运行时自举：子模块拉取 / 受管 Node 安装（全部打桩，不碰网络）."""

from pathlib import Path

from app.infrastructure.lp_tools import runner
from app.infrastructure.rhpools import client


class _FakeProc:
    def __init__(self, code=0, stderr=""):
        self.returncode = code
        self.stderr = stderr
        self.stdout = ""


# ------------------------- runner.ensure_repo -------------------------

def test_runner_ensure_repo_noop_when_present():
    assert runner.tools_available() is True
    assert runner.ensure_repo() is True


def test_runner_ensure_repo_clones_when_missing(monkeypatch, tmp_path):
    calls = {}
    state = {"first": True}

    def fake_avail():
        if state["first"]:
            state["first"] = False
            return False
        return True

    monkeypatch.setattr(runner, "tools_available", fake_avail)
    monkeypatch.setattr(runner, "TOOLS_DIR", tmp_path / "robinhood-chain-lp-tools")
    monkeypatch.setattr(runner.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.ensure_repo() is True
    assert calls["cmd"][0].endswith("git")
    assert calls["cmd"][1:5] == ["clone", "--depth", "1", runner.TOOLS_URL]
    assert str(runner.TOOLS_DIR) == calls["cmd"][5]


def test_runner_ensure_repo_fails_without_git(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "tools_available", lambda: False)
    monkeypatch.setattr(runner, "TOOLS_DIR", tmp_path / "x")
    monkeypatch.setattr(runner.shutil, "which", lambda n: None)
    assert runner.ensure_repo() is False


def test_runner_ensure_repo_refuses_nonempty_broken_dir(monkeypatch, tmp_path):
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "junk.txt").write_text("x")
    monkeypatch.setattr(runner, "tools_available", lambda: False)
    monkeypatch.setattr(runner, "TOOLS_DIR", broken)
    monkeypatch.setattr(runner.shutil, "which", lambda n: "/usr/bin/git")
    assert runner.ensure_repo() is False  # 非空且缺源文件，不敢覆盖


# ------------------------- runner.ensure_node / 受管 PATH -------------------------

def test_node_available_consistent():
    expect = runner.shutil.which("node") is not None or runner.managed_node_bin() is not None
    assert runner.node_available() is expect


def test_latest_node_asset_unsupported_platform(monkeypatch):
    monkeypatch.setattr(runner.platform, "system", lambda: "Windows")
    assert runner._latest_node_asset() is None


def test_latest_node_asset_bad_arch(monkeypatch):
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runner.platform, "machine", lambda: "ppc64le")
    assert runner._latest_node_asset() is None


def test_managed_node_bin_and_env_path(monkeypatch, tmp_path):
    home = tmp_path / "monitor-node"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "node").write_text("#!/bin/sh\n")
    monkeypatch.setattr(runner, "MANAGED_NODE_HOME", home)
    assert runner.managed_node_bin() == home / "bin"
    env = runner._child_env()
    assert env["PATH"].startswith(str(home / "bin"))


def test_child_env_passthrough_without_managed_node(monkeypatch):
    monkeypatch.setattr(runner, "MANAGED_NODE_HOME", Path("/nonexistent-node"))
    env = runner._child_env()
    assert "PATH" in env


def test_missing_parts_all_present(monkeypatch):
    monkeypatch.setattr(runner, "tools_available", lambda: True)
    monkeypatch.setattr(runner, "node_available", lambda: True)
    monkeypatch.setattr(runner, "deps_installed", lambda: True)
    assert runner.missing_parts() == []


def test_missing_parts_reports_submodule(monkeypatch):
    monkeypatch.setattr(runner, "tools_available", lambda: False)
    monkeypatch.setattr(runner, "node_available", lambda: True)
    monkeypatch.setattr(runner, "deps_installed", lambda: True)
    assert runner.missing_parts() == ["子模块代码"]


def test_missing_parts_reports_npm_deps(monkeypatch):
    monkeypatch.setattr(runner, "tools_available", lambda: True)
    monkeypatch.setattr(runner, "node_available", lambda: True)
    monkeypatch.setattr(runner, "deps_installed", lambda: False)
    assert runner.missing_parts() == ["npm 依赖"]


# ------------------------- client.ensure_repo（rhpools） -------------------------

def test_client_ensure_repo_noop_when_present():
    assert client.available() is True
    assert client.ensure_repo() is True


def test_client_ensure_repo_clones_when_missing(monkeypatch, tmp_path):
    calls = {}
    state = {"first": True}

    def fake_avail():
        if state["first"]:
            state["first"] = False
            return False
        return True

    monkeypatch.setattr(client, "available", fake_avail)
    monkeypatch.setattr(client, "MODULE_DIR", tmp_path / "robinhoodpools")
    monkeypatch.setattr(client.shutil, "which", lambda n: "/usr/bin/git" if n == "git" else None)

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    assert client.ensure_repo() is True
    assert calls["cmd"][0].endswith("git")
    assert calls["cmd"][1:5] == ["clone", "--depth", "1", client.REPO_URL]


def test_client_ensure_repo_fails_without_git(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "available", lambda: False)
    monkeypatch.setattr(client, "MODULE_DIR", tmp_path / "x")
    monkeypatch.setattr(client.shutil, "which", lambda n: None)
    assert client.ensure_repo() is False


def test_ensure_running_skips_when_autostart_disabled(monkeypatch):
    """CI（RHP_AUTO_START=0）：不可用时直接返回 None，绝不拉子模块."""
    monkeypatch.setenv("RHP_AUTO_START", "0")
    monkeypatch.setattr(client, "health", lambda *a, **k: None)
    cloned = []
    monkeypatch.setattr(client, "ensure_repo", lambda: cloned.append(1) or True)
    assert client.ensure_running() is None
    assert cloned == []


def test_ensure_running_clones_then_spawns(monkeypatch):
    monkeypatch.delenv("RHP_AUTO_START", raising=False)
    seq = {"health": 0}

    def fake_health(*a, **k):
        seq["health"] += 1
        # 前 1 次（探活）失败，spawn 后第一次轮询成功
        return {"state": "warming"} if seq["health"] >= 2 else None

    monkeypatch.setattr(client, "health", fake_health)
    monkeypatch.setattr(client, "ensure_repo", lambda: True)
    monkeypatch.setattr(client, "_spawn", lambda: True)
    assert client.ensure_running(wait_s=5) == {"state": "warming"}
