import logging
import os

from linuxdo_monitor.logging_setup import cleanup_old_logs, get_logger, setup_logging


def test_setup_logging_creates_hourly_file(tmp_path):
    logger = setup_logging(str(tmp_path))
    assert any(
        isinstance(h, logging.FileHandler)
        for h in logging.getLogger("linuxdo_monitor").handlers
    )
    files = [f for f in os.listdir(tmp_path) if f.startswith("app-") and f.endswith(".log")]
    assert files, "未生成按小时命名的日志文件"


def test_setup_logging_is_idempotent_within_same_hour(tmp_path):
    setup_logging(str(tmp_path))
    first = len(os.listdir(tmp_path))
    setup_logging(str(tmp_path))
    assert len(os.listdir(tmp_path)) == first


def test_cleanup_old_logs_removes_expired(tmp_path):
    log_dir = str(tmp_path)
    old = tmp_path / "app-20200101-00.log"
    old.write_text("old")
    os.utime(old, (0, 0))
    assert cleanup_old_logs(log_dir, retention_hours=4) == 1
    assert not old.exists()


def test_cleanup_old_logs_keeps_recent(tmp_path):
    log_dir = str(tmp_path)
    recent = tmp_path / "app-20990101-00.log"
    recent.write_text("new")
    os.utime(recent, (9_999_999_999, 9_999_999_999))
    assert cleanup_old_logs(log_dir, retention_hours=4) == 0


def test_cleanup_old_logs_ignores_unrelated_files(tmp_path):
    (tmp_path / "other.log").write_text("x")
    (tmp_path / "app-notes.txt").write_text("x")
    assert cleanup_old_logs(str(tmp_path), retention_hours=0) == 0


def test_cleanup_old_logs_missing_dir():
    assert cleanup_old_logs("/no/such/dir", retention_hours=4) == 0


def test_cleanup_old_logs_negative_retention_is_noop(tmp_path):
    (tmp_path / "app-20200101-00.log").write_text("old")
    assert cleanup_old_logs(str(tmp_path), retention_hours=-1) == 0


def test_get_logger_namespacing():
    assert get_logger("app").name == "linuxdo_monitor.app"
    assert get_logger().name == "linuxdo_monitor"
