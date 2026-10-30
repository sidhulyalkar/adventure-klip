from adventure_intelligence.report import write_report
from adventure_intelligence.catalog import connect


def test_empty_report_has_no_private_data(tmp_path):
    root=tmp_path/"workspace"
    with connect(root):
        pass
    out=root/"exports"/"review.html"
    result=write_report(root,out)
    assert result["assets"]==0
    assert "No assets yet" in out.read_text()
