import json
from pathlib import Path
from typing import Any, Dict

class State:
    def __init__(self, path: str = "state.json"):
        self.path = Path(path)
        self.data: Dict[str, Any] = {"jobs": {}}
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {"jobs": {}}

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_job(self, job_id: str, info: Dict[str, Any]):
        self.data.setdefault("jobs", {})[job_id] = info
        self.save()

    def set_job_result(self, job_id: str, result: Dict[str, Any]):
        self.data.setdefault("jobs", {}).setdefault(job_id, {})["result"] = result
        self.save()
