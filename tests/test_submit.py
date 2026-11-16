import tarfile

from gremlin_assess.export import export_public
from workspace_tools.submit import main, submit

GOOD = """\
findings:
  - title: "Limit check is off by one"
    category: bug
    location: "tallybook/tallybook/limits.py:11"
    evidence: "docs say at or above"
    status: fixed
    fix_summary: "changed the comparison"
    checked_by: "ran the tests"
"""


def test_submit_archives_the_workspace(toy, tmp_path):
    workspace, out = tmp_path / "workspace", tmp_path / "out"
    export_public(toy, workspace)
    (workspace / "FINDINGS.yaml").write_text(GOOD, encoding="utf-8")
    cache = workspace / "tallybook" / "tallybook" / "__pycache__"
    cache.mkdir()
    (cache / "limits.cpython-311.pyc").write_bytes(b"\0")

    archive, problems = submit(workspace, out)

    assert problems == []
    assert (out / "SUBMITTED").read_text(encoding="utf-8").split()[1] == archive.name
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "workspace/FINDINGS.yaml" in names
    assert "workspace/tallybook/tallybook/limits.py" in names
    assert not any("__pycache__" in name for name in names)


def test_submit_refuses_a_malformed_findings_file(toy, tmp_path, capsys):
    workspace, out = tmp_path / "workspace", tmp_path / "out"
    export_public(toy, workspace)
    (workspace / "FINDINGS.yaml").write_text(GOOD.replace("category: bug", "category: defect"), encoding="utf-8")

    assert main(["--workspace", str(workspace), "--out", str(out)]) == 1
    assert "field 'category' must be one of" in capsys.readouterr().out
    assert not out.exists()


def test_submit_accepts_the_empty_template(toy, tmp_path, capsys):
    workspace, out = tmp_path / "workspace", tmp_path / "out"
    export_public(toy, workspace)

    assert main(["--workspace", str(workspace), "--out", str(out)]) == 0
    assert "Submitted: submission-" in capsys.readouterr().out
    assert len(list(out.glob("submission-*.tar.gz"))) == 1
