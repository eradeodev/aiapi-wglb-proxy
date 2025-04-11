from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from config_manager import ConfigManager
import requests
import json
import socket
import time
from ascii_colors import ASCIIColors
import datetime
import sys
import traceback

class ProxyRequestHandler(BaseHTTPRequestHandler):
    config_manager = None
    request_logger = None
    deactivate_security = False

    def get_reachable_servers(self):
        """Returns list of servers sorted by queue size and filtered by network reachability"""
        reachable = []
        self.config_manager._load_config()  # Ensure config is up-to-date
        servers = self.config_manager.get_servers()
        for server in servers:
            server_name, config = server
            try:
                if self._is_server_reachable(server_name, config["url"]):
                    # Update available models for the server before considering it reachable
                    available_models = self.get_server_available_models(server_name, config["url"])
                    # (The config manager now holds an up-to-date list of models.)
                    config["available_models"] = available_models
                    reachable.append(server)
            except Exception as e:
                ASCIIColors.yellow(
                    f"Server {server_name} unreachable: {str(e)}"
                )
        return sorted(
            reachable,
            key=lambda s: (s[1]["queue"].qsize(), s[1]['last_processed_time'])
        )

    def get_server_available_models(self, server_name, server_url):
        """
        Queries the server for its available models via a GET request to /api/tags.
        The expected JSON response should have a structure like:
        {
           "models": [
             { "name": "codellama:13b" },
             { "name": "llama3:latest" }
           ]
        }
        On success, updates the server configuration via config_manager and returns the list of model names.
        """
        self.request_logger.log(
            event="retrieving_models",
            user="proxy_server",
            ip_address=self.client_address[0],
            access="Authorized",
            server=server_name,
            nb_queued_requests_on_server=-1,
            response_status=0,
            message="Getting available models from server",
        )
        try:
            response = requests.get(f"{server_url}/api/tags", timeout=10)
            response.raise_for_status()
            data = response.json()
            models_data = data.get("models", [])
            available_models = [model["name"] for model in models_data if "name" in model]
            # Update the configuration
            self.config_manager.update_server_available_models(server_name, available_models)
            return available_models
        except Exception as e:
            ASCIIColors.yellow(f"Failed retrieving models for server {server_name}: {e}")
            return []

    def _is_server_reachable(self, server_name, server_url):
        """Checks if a server is reachable."""
        parsed = urlparse(server_url)
        host = parsed.hostname
        port = parsed.port or (80 if parsed.scheme == "http" else 443)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            try:
                s.connect((host, port))
                return True
            except Exception as e:
                ASCIIColors.yellow(
                    f"Server {server_name} ({host}:{port}) unreachable: {str(e)}"
                )
                return False

    def _send_response_code(self, response_code, message=""):
        server_name = getattr(self, "active_server_name", "unset_server")
        queue_size = getattr(self, 'active_server_queue_size', 'N/A')
        server_log_info = f"{server_name}- Queue Size {queue_size}" if server_name != "unset_server" else "unset_server"
        self.request_logger.log(
            event="response_sent",
            user=getattr(self, 'user', 'unknown'),
            ip_address=self.client_address[0],
            access="Authorized" if getattr(self, 'user', 'unknown') != 'unknown' else 'Denied',
            server=server_log_info,
            nb_queued_requests_on_server=queue_size if isinstance(queue_size, int) else -1,
            response_status=response_code,
            message=message
        )
        self.send_response(response_code, message)

    def _send_response(self, response, message=""):
        server_name = getattr(self, "active_server_name", "unset_server")
        queue_size = getattr(self, 'active_server_queue_size', 'N/A')
        server_log_info = f"{server_name}- Queue Size {queue_size}" if server_name != "unset_server" else "unset_server"
        self.request_logger.log(
            event="response_sent",
            user=getattr(self, 'user', 'unknown'),
            ip_address=self.client_address[0],
            access="Authorized" if getattr(self, 'user', 'unknown') != 'unknown' else 'Denied',
            server=server_log_info,
            nb_queued_requests_on_server=queue_size if isinstance(queue_size, int) else -1,
            response_status=response.status_code,
            message=message
        )
        self.send_response(response.status_code, message)
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
        return

    def do_HEAD(self):
        self._handle_request()

    def do_GET(self):
        self._handle_request()

    def do_POST(self):
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
                if self.config_manager.get_authorized_users().get(user) == key:
                    return True, user
                else:
                    return False, user
            except ValueError:
                return False, token  # Token format is incorrect
        except Exception:
            return False, "unknown"

    def _handle_security(self):
        """Handles the security check for the request."""
        if self.deactivate_security:
            self.user = "anonymous"
            return True
        else:
            authorized, user = self._validate_user()
            self.user = user
            client_ip, _ = self.client_address
            if authorized:
                return True
            else:
                ASCIIColors.red(f"User '{user}' is not authorized")
                self.request_logger.log(
                    event="rejected",
                    user=user,
                    ip_address=client_ip,
                    access="Denied",
                    server="None",
                    nb_queued_requests_on_server=-1,
                    error="Authentication failed",
                )
                self._send_response_code(403, f"User '{user}' is not authorized")
                self.end_headers()
                return False

    def _get_request_data(self):
        """Extracts path, GET parameters, and POST data from the request."""
        url = urlparse(self.path)
        path = url.path
        get_params = parse_qs(url.query) or {}
        post_data = {}
        if self.command in ["POST", "PUT"]:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                post_data = self.rfile.read(content_length)
        return path, get_params, post_data

    def _route_request(self, path, get_params, post_data, reachable_servers):
        """
        Routes the request to a proxy server with retries.
        Implements auto-pulling of missing models for generate endpoints.
        """
        client_ip, _ = self.client_address
        max_retries = 3
        attempt = 0
        tried_servers_overall = []
        proxy_timeout = (60, 3600)  # (connect timeout, read timeout)
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
                self.active_server_queue_size = config["queue"].qsize()
                self.config_manager.update_server_process_time(server_name)

                start_time = time.time()
                try:
                    # For generation endpoints, check if requested model is available
                    if path in ["/api/generate", "/api/embed", "/api/chat", "/v1/chat/completions"]:
                        # Decode post data for model extraction
                        post_data_dict = {}
                        if isinstance(post_data, bytes):
                            post_data_str = post_data.decode("utf-8")
                            try:
                                post_data_dict = json.loads(post_data_str)
                            except json.JSONDecodeError:
                                ASCIIColors.yellow("Could not decode post data as JSON.")
                        
                        model = post_data_dict.get("model")
                        if model:
                            # Check against the server's available_models list
                            if model not in config.get("available_models", []):
                                ASCIIColors.yellow(f"Model '{model}' not available on server {server_name}. Auto-pulling...")
                                try:
                                    pull_response = requests.post(
                                        config["url"] + "/api/pull",
                                        json={"model": model},
                                        timeout=proxy_timeout,
                                    )
                                    pull_response.raise_for_status()
                                    # Optionally re-query the available models after pulling
                                    updated_models = self.get_server_available_models(server_name, config["url"])
                                    config["available_models"] = updated_models
                                    if model not in updated_models:
                                        ASCIIColors.red(f"Model '{model}' still not available after pull on server {server_name}.")
                                        # Continue to next server if pull did not update available models.
                                        continue
                                    else:
                                        ASCIIColors.yellow(f"Successfully pulled model '{model}' on server {server_name}.")
                                except Exception as pull_exception:
                                    ASCIIColors.red(f"Failed to auto-pull model '{model}' on server {server_name}: {pull_exception}")
                                    # If pull fails, try the next available server.
                                    continue

                        load_tracker = config["queue"]
                        self.request_logger.log(
                            event="gen_request",
                            user=self.user,
                            ip_address=client_ip,
                            access="Authorized",
                            server=server_name,
                            nb_queued_requests_on_server=load_tracker.qsize(),
                            request_path=path,
                            request_params=get_params,
                            request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                        )
                        load_tracker.put_nowait(1)
                        try:
                            response = requests.request(
                                self.command,
                                config["url"] + path,
                                params=get_params,
                                data=post_data,
                                stream=post_data_dict.get("stream", False),
                                timeout=proxy_timeout,
                            )
                            response.raise_for_status()
                            self._send_response(response)
                            end_time = time.time()
                            duration = end_time - start_time
                            self.request_logger.log(
                                event="gen_done",
                                user=self.user,
                                ip_address=client_ip,
                                access="Authorized",
                                server=server_name,
                                nb_queued_requests_on_server=load_tracker.qsize(),
                                response_status=response.status_code,
                                duration=duration,
                                request_path=path,
                                request_params=get_params,
                                request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                            )
                            return  # Request successful
                        except requests.exceptions.HTTPError as e:
                            end_time = time.time()
                            duration = end_time - start_time
                            self.request_logger.log(
                                event="gen_error",
                                user=self.user,
                                ip_address=client_ip,
                                access="Authorized",
                                server=server_name,
                                nb_queued_requests_on_server=load_tracker.qsize(),
                                error=f"HTTP error {e.response.status_code}: {e}",
                                response_status=e.response.status_code,
                                duration=duration,
                                request_path=path,
                                request_params=get_params,
                                request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                            )
                            if 400 <= e.response.status_code < 500:
                                self._send_response(e.response, f"Generate request handling failed, GET params={get_params}, POST data={post_data}")
                                return  # Malformed request, no retry
                            # For 5xx errors, we will retry
                        except requests.exceptions.RequestException as e:
                            end_time = time.time()
                            duration = end_time - start_time
                            self.request_logger.log(
                                event="gen_error",
                                user=self.user,
                                ip_address=client_ip,
                                access="Authorized",
                                server=server_name,
                                nb_queued_requests_on_server=load_tracker.qsize(),
                                error=str(e),
                                duration=duration,
                                request_path=path,
                                request_params=get_params,
                                request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                            )
                            self._send_response(e.response, f"Generate request handling failed, GET params={get_params}, POST data={post_data}")
                        finally:
                            load_tracker.get_nowait()
                    elif path == "/api/pull":
                        # For /api/pull, pass the request to all reachable servers.
                        for server_info in reachable_servers:
                            server_name, config = server_info
                            start_time_pull = time.time()
                            try:
                                response = requests.request(
                                    self.command,
                                    config["url"] + path,
                                    params=get_params,
                                    data=post_data,
                                    timeout=proxy_timeout,
                                )
                                self._send_response(response)
                                end_time_pull = time.time()
                                duration_pull = end_time_pull - start_time_pull
                                self.request_logger.log(
                                    event="pull_done",
                                    user=self.user,
                                    ip_address=client_ip,
                                    access="Authorized",
                                    server=server_name,
                                    nb_queued_requests_on_server=-1,
                                    response_status=response.status_code,
                                    duration=duration_pull,
                                    request_path=path,
                                    request_params=get_params,
                                    request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                                )
                            except requests.exceptions.RequestException as e:
                                end_time_pull = time.time()
                                duration_pull = end_time_pull - start_time_pull
                                ASCIIColors.yellow(f"Error pulling from {server_name}: {e}")
                                self.request_logger.log(
                                    event="pull_error",
                                    user=self.user,
                                    ip_address=client_ip,
                                    access="Authorized",
                                    server=server_name,
                                    nb_queued_requests_on_server=-1,
                                    error=str(e),
                                    duration=duration_pull,
                                    request_path=path,
                                    request_params=get_params,
                                    request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                                    response_status=getattr(e.response, 'status_code', None)
                                )
                                self._send_response(getattr(e.response, 'content', b'').decode('utf-8'), f"/api/pull request handling failed, GET params={get_params}, POST data={post_data}")
                        return  # Pull request sent to all reachable servers
                    else:
                        # For all other requests, simply proxy the call.
                        response = requests.request(
                            self.command,
                            config["url"] + path,
                            params=get_params,
                            data=post_data,
                            timeout=proxy_timeout,
                        )
                        start_time_other = time.time()
                        try:
                            response.raise_for_status()
                            self._send_response(response)
                            end_time_other = time.time()
                            duration_other = end_time_other - start_time_other
                            self.request_logger.log(
                                event="default_done",
                                user=self.user,
                                ip_address=client_ip,
                                access="Authorized",
                                server=server_name,
                                nb_queued_requests_on_server=-1,
                                response_status=response.status_code,
                                duration=duration_other,
                                request_path=path,
                                request_params=get_params,
                                request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                            )
                            return  # Request successful
                        except requests.exceptions.HTTPError as e:
                            end_time_other = time.time()
                            duration_other = end_time_other - start_time_other
                            self.request_logger.log(
                                event="default_error",
                                user=self.user,
                                ip_address=client_ip,
                                access="Authorized",
                                server=server_name,
                                nb_queued_requests_on_server=-1,
                                error=f"HTTP error {e.response.status_code}: {e}",
                                response_status=e.response.status_code,
                                duration=duration_other,
                                request_path=path,
                                request_params=get_params,
                                request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                            )
                            if 400 <= e.response.status_code < 500:
                                self._send_response(e.response, f"General request handling failed, GET params={get_params}, POST data={post_data}")
                                return
                        except requests.exceptions.RequestException as e:
                            end_time_other = time.time()
                            duration_other = end_time_other - start_time_other
                            self.request_logger.log(
                                event="default_error",
                                user=self.user,
                                ip_address=client_ip,
                                access="Authorized",
                                server=server_name,
                                nb_queued_requests_on_server=-1,
                                error=str(e),
                                duration=duration_other,
                                request_path=path,
                                request_params=get_params,
                                request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                            )
                except requests.exceptions.ConnectionError as e:
                    end_time = time.time()
                    duration = end_time - start_time
                    ASCIIColors.yellow(f"Could not connect to server {server_name}: {e}")
                    self.request_logger.log(
                        event="connection_error",
                        user=self.user,
                        ip_address=client_ip,
                        access="Authorized",
                        server=server_name,
                        nb_queued_requests_on_server=-1,
                        error=f"Connection error: {e}",
                        duration=duration,
                        request_path=path,
                        request_params=get_params,
                        request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                    )
                except Exception as e:
                    end_time = time.time()
                    duration = end_time - start_time
                    ASCIIColors.yellow(f"An unexpected error occurred while routing to {server_name}: {e}")
                    self.request_logger.log(
                        event="routing_error",
                        user=self.user,
                        ip_address=client_ip,
                        access="Authorized",
                        server=server_name,
                        nb_queued_requests_on_server=-1,
                        error=str(e),
                        duration=duration,
                        request_path=path,
                        request_params=get_params,
                        request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                    )
            tried_servers_overall.extend(tried_servers_this_attempt)

        # If all retries failed
        all_tried_servers = list(set(tried_servers_overall))
        retry_failed_message = f"Failed to process the request on any of the reachable servers after {max_retries} retries: {', '.join(all_tried_servers)}"
        self._send_response_code(503, retry_failed_message)
        self.end_headers()
        ASCIIColors.red(retry_failed_message)
        self.request_logger.log(
            event="error",
            user=self.user,
            ip_address=client_ip,
            access="Denied",
            server="All",
            nb_queued_requests_on_server=-1,
            error=f"Failed on all servers after {max_retries} retries: {', '.join(all_tried_servers)}",
            request_path=path,
            request_params=get_params,
            request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
        )

    def _handle_request(self):
        """Main handler for incoming requests."""
        start_time = time.time()
        client_ip, _ = self.client_address
        try:
            if not self._handle_security():
                return

            path, get_params, post_data = self._get_request_data()
            reachable_servers = self.get_reachable_servers()

            if not reachable_servers:
                not_available_message = f"No reachable Ollama servers available. Reachable Servers: {reachable_servers}, Servers: {self.config_manager.get_servers()}"
                self._send_response_code(503, not_available_message)
                self.end_headers()
                ASCIIColors.red(not_available_message)
                self.request_logger.log(
                    event="error",
                    user=self.user,
                    ip_address=client_ip,
                    access="Denied",
                    server="None",
                    nb_queued_requests_on_server=-1,
                    error="No reachable Ollama servers",
                    request_path=path,
                    request_params=get_params,
                    request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
                )
                return

            self._route_request(path, get_params, post_data, reachable_servers)

        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time
            ASCIIColors.red(f"An unexpected error occurred while handling the request: {e}")
            traceback.print_exc()  # Print the traceback to stderr for debugging
            self._send_response_code(500, "Internal Server Error")
            self.end_headers()
            self.request_logger.log(
                event="error",
                user=getattr(self, 'user', 'unknown'),
                ip_address=client_ip,
                access="Denied",
                server="None",
                nb_queued_requests_on_server=-1,
                error=f"Unexpected error: {e}",
                duration=duration,
                request_path=getattr(self, 'path', 'unknown'),
                request_params=get_params,
                request_body=post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data),
            )
        finally:
            end_time = time.time()
            duration = end_time - start_time
            self.request_logger.log(
                event="request_served",
                user=getattr(self, 'user', 'unknown'),
                ip_address=client_ip,
                access="Authorized" if getattr(self, 'user', 'unknown') != 'unknown' else 'Denied',
                server=getattr(self, 'active_server_name', 'None'),
                nb_queued_requests_on_server=getattr(self, 'active_server_queue_size', -1),
                duration=duration,
                request_path=getattr(self, 'path', 'unknown'),
                request_params=getattr(self, 'get_params', {}),
                request_body=getattr(self, 'post_data', b'').decode('utf-8', errors='ignore') if isinstance(getattr(self, 'post_data', b''), bytes) else str(getattr(self, 'post_data', b'')),
            )

