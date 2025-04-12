import json
from pathlib import Path
import datetime
import sys

class RequestLogger:
    def __init__(self, log_file_path):
        self.log_file_path = Path(log_file_path)
        self._ensure_log_file_exists()

    def _ensure_log_file_exists(self):
        """Create log file if it doesn't exist."""
        self.log_file_path.touch(exist_ok=True)

    def log(self, **kwargs):
        """Log a single request as a JSON object with non-empty values only."""
        log_entry = {
            "time_stamp": datetime.datetime.now().isoformat()
        }

        for key, value in kwargs.items():
            if value not in (None, "", [], {}, 0, -1):
                log_entry[key] = value

        with open(self.log_file_path, mode="a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")

        sys.stderr.write(f"{log_entry}\n")