import json
from pathlib import Path
import datetime
import sys
from ascii_colors import ASCIIColors

class ServerLogger:
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

        color = None

        for key, value in kwargs.items():
            if value not in (None, "", [], {}, 0, -1):
                log_entry[key] = value
            if "error" == key and value not in (None, "", [], {}, 0, -1):
                color = ASCIIColors.RED

        output = json.dumps(log_entry)
        if color:
            output = f"{color}{output}{ASCIIColors.RESET}"
        with open(self.log_file_path, mode="a", encoding="utf-8") as f:
            f.write(output + "\n")

        sys.stderr.write(f"{output}\n")