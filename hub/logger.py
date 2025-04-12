import csv
from pathlib import Path
import datetime
import sys

class RequestLogger:
    FIELDNAMES = [
        "time_stamp",
        "event",
        "user_name",
        "ip_address",
        "access",
        "server",
        "nb_queued_requests_on_server",
        "duration",
        "request_path",
        "request_params",
        "request_body",
        "response_status",
        "error",
        "message",
    ]

    def __init__(self, log_file_path):
        self.log_file_path = Path(log_file_path)
        self._ensure_log_file_exists()

    def _ensure_log_file_exists(self):
        """Ensures the log file exists and writes the header if it's a new file."""
        if not self.log_file_path.exists():
            with open(self.log_file_path, mode="w", newline="") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=self.FIELDNAMES)
                writer.writeheader()

    def log(self, **kwargs):
        """Logs a row to the CSV file, including only non-empty fields."""
        row = {field: "" for field in self.FIELDNAMES}  # Default empty row
        row["time_stamp"] = str(datetime.datetime.now())

        # Fill in only the provided (non-empty) fields
        for key, value in kwargs.items():
            if key in self.FIELDNAMES and value not in (None, "", [], {}, 0):
                row[key] = str(value)

        with open(self.log_file_path, mode="a", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=self.FIELDNAMES)
            writer.writerow(row)

        sys.stderr.write(f"{row}\n")
