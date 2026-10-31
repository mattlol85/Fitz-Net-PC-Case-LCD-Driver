from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from fitzlcd import updater
from fitzlcd.updater import Release, UpdateError

# Trimmed to the fields the checker reads, but shaped like the real response.
PAYLOAD = {
    "tag_name": "v2.0.0",
    "html_url": "https://github.com/mattlol85/Fitz-Net-PC-Case-LCD-Driver/releases/tag/v2.0.0",
    "body": "### What's new\n- Everything",
    "assets": [
        {
            "name": "FitzLCD-2.0.0-windows.zip",
            "size": 12345,
            "browser_download_url": "https://example.invalid/FitzLCD-2.0.0-windows.zip",
        }
    ],
}


class Recorder:
    """Stand-in for the HTTP call; records every request so tests can count them."""

    def __init__(self, *responses: tuple[int, object]) -> None:
        self.responses = list(responses) or [(200, PAYLOAD)]
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict) -> tuple[int, str]:
        self.calls.append((url, headers))
        status, body = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(body, Exception):
            raise body
        return status, body if isinstance(body, str) else json.dumps(body)


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path: Path):
    """Never let a check or a download touch the real %APPDATA%\\FitzLCD."""
    monkeypatch.setenv("FITZLCD_HOME", str(tmp_path / "home"))


def make_build_zip(path: Path, *, exe: bool = True, escape: bool = False) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        if exe:
            archive.writestr("FitzLCD.exe", "MZ fake")
        archive.writestr("_internal/base_library.zip", "x")
        if escape:
            archive.writestr("../evil.txt", "pwned")
    return path


class TestVersions:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("v1.2.0", (1, 2, 0)), ("1.2", (1, 2)), ("", (0,)), (None, (0,)), ("nightly", (0,))],
    )
    def test_parses_tolerantly(self, text, expected):
        assert updater.parse_version(text) == expected

    def test_unparsable_versions_sort_oldest_rather_than_raising(self):
        assert updater.is_newer("1.0.0", "nightly") is True
        assert updater.is_newer("nightly", "1.0.0") is False

    def test_compares_numerically_not_lexically(self):
        assert updater.is_newer("1.10.0", "1.9.0") is True
        assert updater.is_newer("1.2.0", "1.2.0") is False
        assert updater.is_newer("1.1.0", "1.2.0") is False


class TestCheck:
    def test_returns_the_release_when_one_is_newer(self):
        fetch = Recorder()
        release = updater.check(current="1.0.0", fetch=fetch)
        assert release is not None
        assert release.version == "2.0.0"
        assert release.size == 12345
        assert release.url.endswith("FitzLCD-2.0.0-windows.zip")
        assert release.notes.startswith("### What's new")
        assert fetch.calls[0][0] == updater.LATEST_URL

    def test_identifies_itself_so_the_api_call_is_attributable(self):
        fetch = Recorder()
        updater.check(current="1.0.0", fetch=fetch)
        assert fetch.calls[0][1]["User-Agent"] == "FitzLCD/1.0.0"

    def test_picks_the_windows_zip_out_of_several_assets(self):
        payload = dict(PAYLOAD)
        payload["assets"] = [
            {"name": "source.tar.gz", "size": 1, "browser_download_url": "https://x.invalid/a"},
            {
                "name": "FitzLCD-2.0.0-windows.zip",
                "size": 9,
                "browser_download_url": "https://x.invalid/b",
            },
        ]
        release = updater.check(current="1.0.0", fetch=Recorder((200, payload)))
        assert release is not None
        assert release.url == "https://x.invalid/b"

    def test_no_update_when_already_current(self):
        assert updater.check(current="2.0.0", fetch=Recorder()) is None

    def test_no_update_when_the_release_ships_no_windows_zip(self):
        payload = dict(PAYLOAD)
        payload["assets"] = []
        assert updater.check(current="1.0.0", fetch=Recorder((200, payload))) is None


class TestCheckFailureModes:
    """A failed check must be silent, never an exception into a running app."""

    @pytest.mark.parametrize(
        "response",
        [
            (403, {"message": "rate limited"}),
            (404, ""),
            (200, "not json at all"),
            (200, OSError("network down")),
        ],
    )
    def test_returns_none_instead_of_raising(self, response):
        assert updater.check(current="1.0.0", fetch=Recorder(response)) is None


class TestCheckDetailed:
    """A check that cannot finish must say why, never look like "you're up to date"."""

    @pytest.mark.parametrize(
        ("response", "reason"),
        [
            ((403, {"message": "rate limited"}), "HTTP 403"),
            ((500, ""), "HTTP 500"),
            ((200, "not json at all"), "JSONDecodeError"),
            ((200, OSError("network down")), "network down"),
            ((200, ["not", "an", "object"]), "expected kind of data"),
            ((200, {"tag_name": ""}), "no version tag"),
        ],
    )
    def test_failures_carry_their_reason(self, response, reason):
        result = updater.check_detailed(current="1.0.0", fetch=Recorder(response))
        assert result.release is None
        assert reason in result.error

    def test_a_release_without_a_windows_build_is_a_failure_not_current(self):
        payload = dict(PAYLOAD)
        payload["assets"] = []
        result = updater.check_detailed(current="1.0.0", fetch=Recorder((200, payload)))
        assert result.release is None
        assert "no Windows build" in result.error

    def test_a_current_build_has_no_error(self):
        assert updater.check_detailed(current="2.0.0", fetch=Recorder()) == updater.CheckResult()

    def test_a_newer_release_has_no_error(self):
        result = updater.check_detailed(current="1.0.0", fetch=Recorder())
        assert result.release is not None
        assert result.error == ""


class TestCheckLog:
    def test_each_check_appends_one_line(self, tmp_path: Path):
        log = tmp_path / "update-check.log"
        updater.record_check(
            updater.CheckResult(error="GitHub answered HTTP 403"), current="1.5.2", path=log
        )
        updater.record_check(updater.CheckResult(), current="1.5.2", path=log)

        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert lines[0].endswith("running 1.5.2: error: GitHub answered HTTP 403")
        assert lines[1].endswith("running 1.5.2: current")

    def test_an_available_update_is_recorded_with_its_version(self, tmp_path: Path):
        log = tmp_path / "update-check.log"
        release = Release(version="2.0.0", notes="", url="u", size=0, page="p")
        updater.record_check(updater.CheckResult(release=release), current="1.0.0", path=log)
        assert "update available: 2.0.0" in log.read_text(encoding="utf-8")

    def test_defaults_to_the_settings_folder(self, tmp_path: Path):
        # The autouse fixture points FITZLCD_HOME at tmp_path / "home".
        updater.record_check(updater.CheckResult(error="boom"))
        assert (tmp_path / "home" / "update-check.log").is_file()

    def test_an_unwritable_log_never_fails_the_check(self, tmp_path: Path):
        blocker = tmp_path / "not-a-folder"
        blocker.write_text("x", encoding="utf-8")
        updater.record_check(updater.CheckResult(), path=blocker / "nested" / "log.txt")


class TestStage:
    def test_extracts_a_valid_build(self, tmp_path: Path):
        archive = make_build_zip(tmp_path / "build.zip")
        staged = updater.stage(archive, "2.0.0", dest_dir=tmp_path / "updates")
        assert (staged / "FitzLCD.exe").is_file()
        assert (staged / "_internal").is_dir()

    def test_rejects_an_archive_that_is_not_a_fitzlcd_build(self, tmp_path: Path):
        archive = make_build_zip(tmp_path / "build.zip", exe=False)
        with pytest.raises(UpdateError, match="does not look like a FitzLCD build"):
            updater.stage(archive, "2.0.0", dest_dir=tmp_path / "updates")

    def test_rejects_paths_that_escape_the_staging_directory(self, tmp_path: Path):
        archive = make_build_zip(tmp_path / "build.zip", escape=True)
        with pytest.raises(UpdateError, match="unsafe path"):
            updater.stage(archive, "2.0.0", dest_dir=tmp_path / "updates")
        assert not (tmp_path / "updates" / "evil.txt").exists()

    def test_rejects_a_corrupt_download(self, tmp_path: Path):
        archive = tmp_path / "build.zip"
        archive.write_bytes(b"this is not a zip")
        with pytest.raises(UpdateError, match="not a valid zip"):
            updater.stage(archive, "2.0.0", dest_dir=tmp_path / "updates")


class TestHelperScript:
    def test_script_swaps_the_install_and_relaunches(self, tmp_path: Path):
        staged = tmp_path / "staged-2.0.0"
        staged.mkdir()
        install = tmp_path / "install"
        install.mkdir()

        script = updater.write_helper(staged, target=install, pid=4242)
        body = script.read_text(encoding="utf-8")

        assert script.parent == staged.parent  # never inside what it deletes
        assert "4242" in body  # waits for us to exit
        assert str(staged) in body
        assert str(install.resolve()) in body
        assert "robocopy" in body
        assert "FitzLCD.exe" in body
        assert "rmdir" in body  # cleans up after itself

    def test_helper_is_detached_so_it_outlives_the_app(self, tmp_path: Path, monkeypatch):
        staged = tmp_path / "staged-2.0.0"
        staged.mkdir()
        calls: list[dict] = []
        monkeypatch.setattr(
            updater.subprocess, "Popen", lambda cmd, **kw: calls.append({"cmd": cmd, **kw})
        )

        script = updater.apply_and_restart(staged, target=tmp_path / "install")

        assert script.exists()
        assert calls[0]["cmd"][-1] == str(script)
        assert calls[0]["creationflags"] != 0
        assert calls[0]["close_fds"] is True


class TestGuards:
    def test_not_frozen_outside_the_packaged_build(self):
        assert updater.is_frozen() is False

    def test_unwritable_install_is_detected_rather_than_assumed(self, tmp_path: Path):
        assert updater.install_writable(tmp_path) is True
        assert updater.install_writable(tmp_path / "does-not-exist") is False

    def test_cleanup_removes_leftovers_and_tolerates_a_missing_dir(self, tmp_path: Path):
        directory = tmp_path / "updates"
        updater.cleanup(directory)  # must not raise

        directory.mkdir()
        (directory / "FitzLCD-1.0.0-windows.zip").write_bytes(b"x")
        (directory / "staged-1.0.0").mkdir()
        updater.cleanup(directory)
        assert list(directory.iterdir()) == []


class TestDownload:
    def test_verifies_the_byte_count(self, tmp_path: Path, monkeypatch):
        release = Release(
            version="2.0.0",
            notes="",
            url="https://example.invalid/FitzLCD-2.0.0-windows.zip",
            size=999,
            page="https://example.invalid",
        )

        class FakeResponse:
            headers = {"Content-Length": "3"}

            def read(self, _size=None):
                chunk, self._done = (b"abc" if not getattr(self, "_done", False) else b""), True
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **kw: FakeResponse())
        with pytest.raises(UpdateError, match="expected 999"):
            updater.download(release, dest_dir=tmp_path)
        # A short download must not be left behind looking complete.
        assert list(tmp_path.iterdir()) == []
