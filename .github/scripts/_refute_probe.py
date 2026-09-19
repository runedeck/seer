import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import runeseer_summary as SUMMARY
import test_runeseer_summary as T

ledger = T.ControllerTests().ledger(threads=[T.thread_node("PRRT_2", "cursor", 21)])
data = T.verdict()
data["generation"] = 1
data["lane_judgments"] = [{
    "path": "src/lib.rs", "line": 7, "summary": "Unchecked error", "lane": "cursor",
    "judgment": "already addressed", "severity": "medium", "reason": "Fixed on the head.",
    "comment_id": 21,
}]
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "verdict.json").write_text(json.dumps(data))
    (root / "summary.md").write_text("**Approve.** Nothing open.")
    (root / "ledger.json").write_text(json.dumps(ledger))
    SUMMARY.format_review(root / "verdict.json", root / "summary.md", T.SHA, 1, T.RUN_URL,
                          ledger_path=root / "ledger.json")
    on_disk = json.loads((root / "verdict.json").read_text())
    print("on-disk thread_dispositions:", on_disk.get("thread_dispositions"))
    applied = SUMMARY.apply_verdict_to_ledger(json.loads((root / "ledger.json").read_text()), on_disk)
    print("ledger thread dispositions:", [(t["id"], t["disposition"]) for t in applied["threads"]])
