"""
project: ollama_proxy_server
file: main.py
author: ParisNeo
description: This is a proxy server that adds a security layer to one or multiple ollama servers and routes the requests to the right server in order to minimize the charge of the server.
"""

import configparser
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from queue import Queue
import requests
import argparse
from ascii_colors import ASCIIColors
from pathlib import Path
import csv
import datetime
import socket
import threading
import sys
import time

def get_servers_from_config(filename):
    config = configparser.ConfigParser()
    config.read(filename)
    return [
            (name, {"url": config[name]["url"], "queue": Queue(), "last_processed_time": 0})
        for name in config.sections()
    ]


# Read the authorized users and their keys from a file

threadlock = threading.Lock()
servers = []

def update_server_process_time(server_name):
    with threadlock:
        global servers
        for name, data in servers:
            if name == server_name:
                data['last_processed_time'] = time.time()

def update_servers_from_config(filename):
    with threadlock:
        global servers
        # Create a dictionary from the current servers for easy lookup
        current_servers_dict = {name: data for name, data in servers}
        
        # Load new configuration
        new_config = get_servers_from_config(filename)
        
        # Build the updated server list
        updated_servers = []
        for name, new_data in new_config:
            if name in current_servers_dict:
                # Preserve existing queue
                old_data = current_servers_dict[name]
                if old_data["url"] == new_data["url"]:
                    # No URL change, keep old data
                    updated_servers.append((name, old_data))
                else:
                    # URL changed, keep old queue but update URL
                    updated_servers.append((name, {"url": new_data["url"], "queue": old_data["queue"], "last_processed_time": old_data["last_processed_time"]}))
            else:
                # New server, add with new queue
                updated_servers.append((name, new_data))

        # Replace the global servers list with the updated one
        servers = updated_servers


def get_authorized_users(filename):
    with open(filename, "r") as f:
        lines = f.readlines()
    authorized_users = {}
    for line in lines:
        if line == "":
            continue
        try:
            user, key = line.strip().split(":")
            authorized_users[user] = key
        except:
            ASCIIColors.red(f"User entry broken:{line.strip()}")
    return authorized_users


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="config.ini", help="Path to the authorized users list"
    )
    parser.add_argument(
        "--log_path", default="access_log.txt", help="Path to the access log file"
    )
    parser.add_argument(
        "--users_list", default="authorized_users.txt", help="Path to the config file"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port number for the server"
    )
    parser.add_argument(
        "-d", "--deactivate_security", action="store_true", help="Deactivates security"
    )
    args = parser.parse_args()
    global servers
    servers = get_servers_from_config(args.config)
    authorized_users = get_authorized_users(args.users_list)
    deactivate_security = args.deactivate_security
    ASCIIColors.red("Ollama Proxy server")
    ASCIIColors.red("Author: ParisNeo")

    class RequestHandler(BaseHTTPRequestHandler):
        def get_reachable_servers(self):
            """Returns list of servers sorted by queue size and filtered by network reachability"""
            reachable = []
            update_servers_from_config(args.config)
            for server in servers:
                server_name, config = server
                try:
                    if self._is_server_reachable(server_name, config["url"]):
                        reachable.append(server)
                except Exception as e:
                    ASCIIColors.yellow(
                        f"Server {server_name} unreachable: {str(e)}"
                    )
            return sorted(
                    reachable,
                    key=lambda s: (s[1]["queue"].qsize(), s[1]['last_processed_time'])
                    )

        def _is_server_reachable(self, server_name, server_url):
            """Checks if a server is reachable."""
            parsed = urlparse(server_url)
            host = parsed.hostname
            port = parsed.port or (80 if parsed.scheme == "http" else 443)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3)
                try:
                    s.connect((host, port))
                    return True
                except Exception as e:
                    ASCIIColors.yellow(
                        f"Server {server_name} ({host}:{port}) unreachable: {str(e)}"
                    )
                    return False

        def _ensure_log_file_exists(self, log_file_path):
            """Ensures the log file exists and writes the header if it's a new file."""
            if not log_file_path.exists():
                with open(log_file_path, mode="w", newline="") as csvfile:
                    fieldnames = [
                        "time_stamp",
                        "event",
                        "user_name",
                        "ip_address",
                        "access",
                        "server",
                        "nb_queued_requests_on_server",
                        "error",
                    ]
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()

        def add_access_log_entry(
            self,
            event,
            user,
            ip_address,
            access,
            server,
            nb_queued_requests_on_server,
            error="",
        ):
            log_file_path = Path(args.log_path)
            self._ensure_log_file_exists(log_file_path)
            with open(log_file_path, mode="a", newline="") as csvfile:
                fieldnames = [
                    "time_stamp",
                    "event",
                    "user_name",
                    "ip_address",
                    "access",
                    "server",
                    "nb_queued_requests_on_server",
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
                    "error": error,
                }
                writer.writerow(row)

        def _send_response(self, response):
            self.send_response(response.status_code)
            for key, value in response.headers.items():
                if key.lower() not in [
                    "content-length",
                    "transfer-encoding",
                    "content-encoding",
                ]:
                    self.send_header(key, value)
            self.end_headers()

            try:
                content = response.content
                self.wfile.write(content)
                self.wfile.flush()
            except BrokenPipeError:
                pass

        def log_message(self, format, *args):
            server_name = getattr(self, "active_server_name", "")
            # Add the server name to the log
            sys.stderr.write("%s - %s\n" % (server_name, format % args))


        def do_HEAD(self):
            self.log_request()
            self._handle_request()

        def do_GET(self):
            self.log_request()
            self._handle_request()

        def do_POST(self):
            self.log_request()
            self._handle_request()

        def _validate_user(self):
            """Validates the user based on the Authorization header."""
            try:
                auth_header = self.headers.get("Authorization")
                if not auth_header or not auth_header.startswith("Bearer "):
                    return False, "unknown"
                token = auth_header.split(" ")[1]
                try:
                    user, key = token.split(":")
                    if authorized_users.get(user) == key:
                        return True, user
                    else:
                        return False, user
                except ValueError:
                    return False, token  # Token format is incorrect
            except Exception:
                return False, "unknown"

        def _handle_security(self):
            """Handles the security check for the request."""
            if deactivate_security:
                self.user = "anonymous"
                return True
            else:
                authorized, user = self._validate_user()
                self.user = user
                if authorized:
                    return True
                else:
                    ASCIIColors.red(f"User '{user}' is not authorized")
                    client_ip, client_port = self.client_address
                    self.add_access_log_entry(
                        event="rejected",
                        user=user,
                        ip_address=client_ip,
                        access="Denied",
                        server="None",
                        nb_queued_requests_on_server=-1,
                        error="Authentication failed",
                    )
                    self.send_response(403)
                    self.end_headers()
                    return False

        def _get_request_data(self):
            """Extracts path, GET parameters, and POST data from the request."""
            url = urlparse(self.path)
            path = url.path
            get_params = parse_qs(url.query) or {}
            if self.command == "POST":
                content_length = int(self.headers["Content-Length"])
                post_data = self.rfile.read(content_length)
                return path, get_params, post_data
            else:
                return path, get_params, {}

        def _route_request(self, path, get_params, post_data, reachable_servers):
            """Routes the request to a proxy server, retrying the request if an internal server exception occurs, or a server cannot be reached, in a round-robin fashion up to 3 times. All other exceptions will return to the caller immediately the error."""
            client_ip, client_port = self.client_address
            max_retries = 3
            attempt = 0
            tried_servers_overall = []

            while attempt < max_retries:
                attempt += 1
                tried_servers_this_attempt = []
                num_servers = len(reachable_servers)
                if not num_servers:
                    break  # No reachable servers, no point in retrying

                start_index = attempt - 1  # Start from the beginning on the first attempt
                for i in range(num_servers):
                    server_index = (start_index + i) % num_servers
                    server_info = reachable_servers[server_index]
                    server_name, config = server_info
                    tried_servers_this_attempt.append(server_name)
                    self.active_server_name = server_name
                    update_server_process_time(server_name)

                    try:
                        if path in ["/api/generate", "/api/embed", "/api/chat", "/v1/chat/completions"]:
                            load_tracker = config["queue"]
                            self.add_access_log_entry(
                                event="gen_request",
                                user=self.user,
                                ip_address=client_ip,
                                access="Authorized",
                                server=server_name,
                                nb_queued_requests_on_server=load_tracker.qsize(),
                            )
                            load_tracker.put_nowait(1)
                            try:
                                post_data_dict = {}
                                if isinstance(post_data, bytes):
                                    post_data_str = post_data.decode("utf-8")
                                    try:
                                        post_data_dict = json.loads(post_data_str)
                                    except json.JSONDecodeError:
                                        ASCIIColors.yellow("Could not decode post data as JSON.")

                                response = requests.request(
                                    self.command,
                                    config["url"] + path,
                                    params=get_params,
                                    data=post_data,
                                    stream=post_data_dict.get("stream", False),
                                )
                                response.raise_for_status()
                                self._send_response(response)
                                self.add_access_log_entry(
                                    event="gen_done",
                                    user=self.user,
                                    ip_address=client_ip,
                                    access="Authorized",
                                    server=server_name,
                                    nb_queued_requests_on_server=load_tracker.qsize(),
                                )
                                return  # Request successful
                            except requests.exceptions.HTTPError as e:
                                self.add_access_log_entry(
                                    event="gen_error",
                                    user=self.user,
                                    ip_address=client_ip,
                                    access="Authorized",
                                    server=server_name,
                                    nb_queued_requests_on_server=load_tracker.qsize(),
                                    error=f"HTTP error {e.response.status_code}: {e}",
                                )
                                if 400 <= e.response.status_code < 500:
                                    self._send_response(e.response)
                                    return  # Malformed request, no retry
                                # For 5xx errors, we will retry
                            except requests.exceptions.RequestException as e:
                                self.add_access_log_entry(
                                    event="gen_error",
                                    user=self.user,
                                    ip_address=client_ip,
                                    access="Authorized",
                                    server=server_name,
                                    nb_queued_requests_on_server=load_tracker.qsize(),
                                    error=str(e),
                                )
                            finally:
                                load_tracker.get_nowait()
                        elif path == "/api/pull":
                            # @Work @HI Increase robustness of api/pull logic as currently if a spoke is down when this goes out it won't get the model -- instead when spokes connect to the hub we should query the models they have, and when a request for a given model comes in it should check if any spokes are missing it and pull it on those spokes, ensuring all eventually pull used models.
                            for server_info in reachable_servers:
                                server_name, config = server_info
                                try:
                                    response = requests.request(
                                        self.command,
                                        config["url"] + path,
                                        params=get_params,
                                        data=post_data,
                                    )
                                    self._send_response(response)
                                except requests.exceptions.RequestException as e:
                                    ASCIIColors.yellow(f"Error pulling from {server_name}: {e}")
                                    self.add_access_log_entry(
                                        event="pull_error",
                                        user=self.user,
                                        ip_address=client_ip,
                                        access="Authorized",
                                        server=server_name,
                                        nb_queued_requests_on_server=-1,
                                        error=str(e),
                                    )
                            return  # Pull request sent to all reachable servers
                        else:
                            response = requests.request(
                                self.command,
                                config["url"] + path,
                                params=get_params,
                                data=post_data,
                            )
                            try:
                                response.raise_for_status()
                                self._send_response(response)
                                return  # Request successful
                            except requests.exceptions.HTTPError as e:
                                self.add_access_log_entry(
                                    event="default_error",
                                    user=self.user,
                                    ip_address=client_ip,
                                    access="Authorized",
                                    server=server_name,
                                    nb_queued_requests_on_server=-1,
                                    error=f"HTTP error {e.response.status_code}: {e}",
                                )
                                if 400 <= e.response.status_code < 500:
                                    self._send_response(e.response)
                                    return  # Malformed request, no retry
                                # For 5xx errors, we will retry
                            except requests.exceptions.RequestException as e:
                                self.add_access_log_entry(
                                    event="default_error",
                                    user=self.user,
                                    ip_address=client_ip,
                                    access="Authorized",
                                    server=server_name,
                                    nb_queued_requests_on_server=-1,
                                    error=str(e),
                                )
                    except requests.exceptions.ConnectionError as e:
                        ASCIIColors.yellow(f"Could not connect to server {server_name}: {e}")
                        self.add_access_log_entry(
                            event="connection_error",
                            user=self.user,
                            ip_address=client_ip,
                            access="Authorized",
                            server=server_name,
                            nb_queued_requests_on_server=-1,
                            error=f"Connection error: {e}",
                        )
                    except Exception as e:
                        ASCIIColors.yellow(f"An unexpected error occurred while routing to {server_name}: {e}")
                        self.add_access_log_entry(
                            event="routing_error",
                            user=self.user,
                            ip_address=client_ip,
                            access="Authorized",
                            server=server_name,
                            nb_queued_requests_on_server=-1,
                            error=str(e),
                        )

                tried_servers_overall.extend(tried_servers_this_attempt)

            # If all retries failed
            self.send_response(503)
            self.end_headers()
            all_tried_servers = list(set(tried_servers_overall))
            ASCIIColors.red(f"Failed to process the request on any of the reachable servers after {max_retries} retries: {', '.join(all_tried_servers)}")
            self.add_access_log_entry(
                event="error",
                user=self.user,
                ip_address=client_ip,
                access="Denied",
                server="All",
                nb_queued_requests_on_server=-1,
                error=f"Failed on all servers after {max_retries} retries: {', '.join(all_tried_servers)}",
            )

        def _handle_request(self):
            """Main handler for incoming requests."""
            if not self._handle_security():
                return

            path, get_params, post_data = self._get_request_data()
            reachable_servers = self.get_reachable_servers()

            if not reachable_servers:
                self.send_response(503)
                self.end_headers()
                ASCIIColors.red("No reachable Ollama servers available.")
                client_ip, client_port = self.client_address
                self.add_access_log_entry(
                    event="error",
                    user=self.user,
                    ip_address=client_ip,
                    access="Denied",
                    server="None",
                    nb_queued_requests_on_server=-1,
                    error="No reachable Ollama servers",
                )
                return

            self._route_request(path, get_params, post_data, reachable_servers)

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        pass

    print("Starting server")
    server = ThreadedHTTPServer(
        ("", args.port), RequestHandler
    )  # Set the entry port here.
    print(f"Running server on port {args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
