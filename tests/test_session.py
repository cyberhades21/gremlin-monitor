import io
import tarfile
from datetime import datetime, timezone

import pytest

from gremlin_assess.session import (
    SessionError,
    add_intervention,
    collect,
    load_meta,
    missing_artifacts,
    new_session,
    snapshot,
    start_session,
    update_tracker,
    write_walkthrough,
)
from pilot_helpers import finding, make_submission

NOW = datetime(2026, 11, 18, 10, 30, 0, tzinfo=timezone.utc)


def tracker(pilot):
    return (pilot / "tracker.csv").read_bytes().decode("utf-8").splitlines()


def test_a_new_session_has_a_folder_meta_and_a_tracker_row(toy, tmp_path):
    directory = new_session(tmp_path, "S-001", toy, "C-01", key_id="key-1")

    assert sorted(path.name for path in directory.iterdir()) == ["grading", "meta.yaml", "snapshots", "transcript"]
    meta = load_meta(directory)
    assert (meta.package_id, meta.package_version, meta.agent_config) == ("pkg-toy", "0.1.0", "ac-test")
    assert (meta.agent_source, meta.status, meta.key_id) == ("ours", "scheduled", "key-1")
    assert tracker(tmp_path) == [
        "session,candidate,package,agent_source,status,start,end,walkthrough,survey",
        "S-001,C-01,pkg-toy@0.1.0,ours,scheduled,,,,",
    ]


@pytest.mark.parametrize("session_id, candidate", [("S-1", "C-01"), ("s-001", "C-01"), ("S-001", "C-1"), ("S-001", "Jane")])
def test_ids_must_have_the_standard_shape(toy, tmp_path, session_id, candidate):
    with pytest.raises(SessionError):
        new_session(tmp_path, session_id, toy, candidate)


def test_a_session_cannot_be_created_twice(toy, tmp_path):
    new_session(tmp_path, "S-001", toy, "C-01")
    with pytest.raises(SessionError, match="already exists"):
        new_session(tmp_path, "S-001", toy, "C-02")


def test_start_and_interventions_are_recorded(toy, tmp_path):
    directory = new_session(tmp_path, "S-001", toy, "C-01")
    start_session(tmp_path, "S-001", NOW)
    add_intervention(tmp_path, "S-001", "agent outage, paused 6 minutes", NOW)

    meta = load_meta(directory)
    assert (meta.status, meta.start) == ("running", "2026-11-18T10:30:00Z")
    assert [(i.time, i.note) for i in meta.interventions] == [("2026-11-18T10:30:00Z", "agent outage, paused 6 minutes")]
    assert tracker(tmp_path)[1].split(",")[4:6] == ["running", "2026-11-18T10:30:00Z"]


def test_the_tracker_updates_rows_in_order_and_refuses_unknown_columns(tmp_path):
    update_tracker(tmp_path, "S-002", status="running")
    update_tracker(tmp_path, "S-001", status="graded", survey="done")
    update_tracker(tmp_path, "S-002", walkthrough="consistent")

    assert tracker(tmp_path)[1:] == ["S-001,,,,graded,,,,done", "S-002,,,,running,,,consistent,"]
    with pytest.raises(SessionError, match="no column"):
        update_tracker(tmp_path, "S-001", favourite_colour="red")


def test_a_snapshot_is_a_compressed_copy_without_caches(toy, tmp_path):
    directory = new_session(tmp_path, "S-001", toy, "C-01")
    workspace = tmp_path / "live"
    (workspace / "repo" / "__pycache__").mkdir(parents=True)
    (workspace / "repo" / "__pycache__" / "a.pyc").write_bytes(b"x")
    (workspace / "repo" / "a.py").write_bytes(b"print(1)\n")

    archive = snapshot(workspace, directory, NOW)

    assert archive.name == "20261118T103000Z.tar.gz" and archive.parent == directory / "snapshots"
    with tarfile.open(archive) as tar:
        assert tar.getnames() == ["workspace", "workspace/repo", "workspace/repo/a.py"]


def test_collect_moves_a_submission_into_the_session(toy, tmp_path):
    directory = new_session(tmp_path, "S-001", toy, "C-01")
    archive = make_submission(toy, tmp_path, "sub", ["G-001"], [finding("Off by one", "bug", "tallybook/limits.py:11")])

    assert collect(tmp_path, "S-001", archive, NOW) == []

    assert (directory / "final" / "tallybook" / "tallybook" / "limits.py").is_file()
    assert (directory / "final" / "ARCHITECTURE.md").is_file()
    assert b"Off by one" in (directory / "FINDINGS.yaml").read_bytes()
    meta = load_meta(directory)
    assert (meta.status, meta.end) == ("submitted", "2026-11-18T10:30:00Z")


def test_collect_reports_a_bad_findings_file_but_still_collects(toy, tmp_path):
    directory = new_session(tmp_path, "S-001", toy, "C-01")
    bad = finding("Off by one", "defect", "tallybook/limits.py:11")
    archive = make_submission(toy, tmp_path, "sub", [], [])
    workspace = tmp_path / "sub" / "workspace"
    from pilot_helpers import findings_yaml

    (workspace / "FINDINGS.yaml").write_bytes(findings_yaml([bad]))

    problems = collect(tmp_path, "S-001", workspace)

    assert problems == ["FINDINGS.yaml: finding 1: field 'category' must be one of bug, doc_error, test_error, other"]
    assert (directory / "final").is_dir() and archive.is_file()


def _tar_with(tmp_path, member: tarfile.TarInfo, data: bytes = b"") -> object:
    path = tmp_path / "evil.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        tar.addfile(member, io.BytesIO(data))
    return path


def test_a_submission_cannot_write_outside_its_folder(toy, tmp_path):
    new_session(tmp_path, "S-001", toy, "C-01")
    member = tarfile.TarInfo("../outside.txt")
    member.size = 1

    with pytest.raises(SessionError, match="outside its folder"):
        collect(tmp_path, "S-001", _tar_with(tmp_path, member, b"x"))

    assert not (tmp_path / "sessions" / "outside.txt").exists() and not (tmp_path / "outside.txt").exists()


def test_a_submission_cannot_hold_links(toy, tmp_path):
    new_session(tmp_path, "S-001", toy, "C-01")
    member = tarfile.TarInfo("workspace/link")
    member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"

    with pytest.raises(SessionError, match="link or special file"):
        collect(tmp_path, "S-001", _tar_with(tmp_path, member))


def test_missing_artifacts_by_stage(toy, tmp_path):
    directory = new_session(tmp_path, "S-001", toy, "C-01")

    assert missing_artifacts(directory, "collected") == [
        "final", "FINDINGS.yaml", "transcript", "snapshots", "terminal.* (the terminal recording)",
    ]
    (directory / "terminal.cast").write_bytes(b"x")
    (directory / "transcript" / "agent.log").write_bytes(b"x")
    assert "terminal.* (the terminal recording)" not in missing_artifacts(directory, "collected")
    assert missing_artifacts(directory, "complete") == [
        "final", "FINDINGS.yaml", "snapshots",
        "grading/grade.json", "grading/matches.json", "report.md", "walkthrough.md",
    ]
    with pytest.raises(SessionError, match="unknown stage"):
        missing_artifacts(directory, "finished")


def test_the_walkthrough_result_rules(toy, tmp_path):
    directory = new_session(tmp_path, "S-001", toy, "C-01")

    with pytest.raises(SessionError, match="needs notes"):
        write_walkthrough(tmp_path, "S-001", "doubtful")
    with pytest.raises(SessionError, match="must be"):
        write_walkthrough(tmp_path, "S-001", "great")
    path = write_walkthrough(tmp_path, "S-001", "doubtful", "could not explain the fix in limits.py")

    assert path.read_bytes().decode("utf-8") == "Result: doubtful\n\ncould not explain the fix in limits.py\n"
    assert tracker(tmp_path)[1].split(",")[7] == "doubtful" and directory.is_dir()
