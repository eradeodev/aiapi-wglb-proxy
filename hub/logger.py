# file: logger.py
import csv
from pathlib import Path
import datetime

class RequestLogger:
    def __init__(self, log_file_path):
        self.log_file_path = Path(log_file_path)
        self._ensure_log_file_exists()

    def _ensure_log_file_exists(self):
        """Ensures the log file exists and writes the header if it's a new file."""
        if not self.log_file_path.exists():
            with open(self.log_file_path, mode="w", newline="") as csvfile:
                fieldnames = [
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
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()

    def log(
        self,
        event,
        user,
        ip_address,
        access,
        server,
        nb_queued_requests_on_server,
        duration=None,
        request_path=None,
        request_params=None,
        request_body=None,
        response_status=None,
        error="",
    ):
        with open(self.log_file_path, mode="a", newline="") as csvfile:
            fieldnames = [
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
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            row = {
                "time_stamp": str(datetime.datetime.now()),
                "event": event,
                "user_name": user,
                "ip_address": ip_address,
                "access": access,
                "server": server,
                "nb_queued_requests_on_server": nb_queued_requests_on_server,
                "duration": duration if duration is not None else "",
                "request_path": request_path if request_path is not None else "",
                "request_params": str(request_params) if request_params is not None else "",
                "request_body": request_body if request_body is not None else "",
                "response_status": response_status if response_status is not None else "",
                "error": error,
            }
            writer.writerow(row)