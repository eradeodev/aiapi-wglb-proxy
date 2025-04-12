from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
import json
import socket
import time
from ascii_colors import ASCIIColors
import traceback

_GENERATE_PATHS = {"/api/generate", "/api/embed", "/api/chat", "/v1/chat/completions"}
_PROXY_TIMEOUT = (60, 3600)  # (connect timeout, read timeout)
_MAX_RETRIES = 3
class ProxyRequestHandler(BaseHTTPRequestHandler):
    config_manager = None
    request_logger = None
    deactivate_security = False


    def _normalize_model_name(self, name):
        """Removes ':latest' suffix from a model name if present."""
        if name and name.endswith(':latest'):
            # Return the name without the last 7 characters (':latest')
            return name[:-7]
        return name

    def get_reachable_servers(self, path):
        """Returns list of servers sorted by queue size and filtered by network reachability and ability to serve the given request path"""
        reachable = []
        self.config_manager._load_config()  # Ensure config is up-to-date
        servers = self.config_manager.get_servers()
        for server in servers:
            server_name, config = server
            try:
                if self._is_server_reachable(server_name, config["url"]):
                    # Only continue if server handles this request type
                    enabled = config.get("enabled_for_requests", [])
                    # An empty enabled value indicates all request are enabled; otherwise check if path is in enabled
                    ASCIIColors.yellow(
                        f"Server {server_name} enabled_for_requests = {enabled}"
                    )
                    if not enabled or path in enabled:
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

            self.request_logger.log(
                event="retrieving_models",
                user="proxy_server",
                ip_address=self.client_address[0],
                access="Authorized",
                server=server_name,
                nb_queued_requests_on_server=-1,
                response_status=0,
                message=f"Retrieved these models from {server_name}: {available_models}",
            )

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

    def match_model(self, model, models):
        # Try exact match
        matched_model = next((m for m in models if m == model), None)

        # Try normalized match
        normalized_model = self._normalize_model_name(model)
        if not matched_model:
            matched_model = next((m for m in models if self._normalize_model_name(m) == normalized_model), None)

        # If no exact match, try substring (fuzzy) match
        if not matched_model:
            matched_model = next((m for m in models if normalized_model in self._normalize_model_name(m)), None)
        return matched_model



    # --- Helper Methods ---

    def _log_request_outcome(self, event, server_name, path, get_params, post_data,
                            client_ip, start_time, response=None, error=None,
                            queue_size=-1, access="Authorized"):
        """Centralized logging for request outcomes."""
        duration = time.time() - start_time if start_time else None
        post_body_str = post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data)
        status = getattr(response, 'status_code', None)

        log_data = {
            "event": event,
            "user": self.user,
            "ip_address": client_ip,
            "access": access,
            "server": server_name,
            "nb_queued_requests_on_server": queue_size,
            "request_path": path,
            "request_params": get_params,
            "request_body": post_body_str,
        }
        if status is not None:
            log_data["response_status"] = status
        if error is not None:
            # Format HTTPError specifically if possible
            if isinstance(error, requests.exceptions.HTTPError) and error.response is not None:
                log_data["error"] = f"HTTP error {error.response.status_code}: {error}"
                log_data["response_status"] = error.response.status_code # Ensure status is set
            else:
                log_data["error"] = str(error)

        if duration is not None:
            log_data["duration"] = duration

        self.request_logger.log(**log_data)

    def _decode_post_data(self, post_data):
        """Safely decodes post data bytes to a dictionary."""
        if isinstance(post_data, bytes):
            try:
                post_data_str = post_data.decode("utf-8")
                return json.loads(post_data_str)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                ASCIIColors.yellow(f"Could not decode post data as JSON: {e}")
                return {}
        # If already dict or other non-bytes format, handle gracefully
        if isinstance(post_data, dict):
            return post_data
        if post_data is None:
            return {}
        # Attempt to load if it's a string representation of JSON
        if isinstance(post_data, str):
            try:
                return json.loads(post_data)
            except json.JSONDecodeError:
                ASCIIColors.yellow("Could not decode string post data as JSON.")
                return {}
        ASCIIColors.yellow(f"Unexpected post_data type: {type(post_data)}")
        return {} # Return empty dict for unknown types


    def _handle_model_check_and_pull(self, server_name, config, post_data):
        """
        Checks model availability for generate paths, attempts auto-pull if missing.
        Returns potentially updated post_data (bytes), or None if model unavailable/pull failed.
        """
        post_data_dict = self._decode_post_data(post_data)
        model = post_data_dict.get("model")

        if not model:
            # No model specified, proceed with original post_data
            return post_data

        server_url = config["url"]
        try:
            # 1. Check available models
            available_models = self.get_server_available_models(server_name, server_url)
            config["available_models"] = available_models # Update cache/info

            matched_model = self.match_model(model, available_models)

            if matched_model:
                ASCIIColors.yellow(f"{server_name} found matched model for '{model}': '{matched_model}'")
                if model != matched_model:
                    post_data_dict["model"] = matched_model
                    return json.dumps(post_data_dict).encode("utf-8")
                return post_data # Model available and matched (or was already exact)

            # 2. Model not found, attempt pull
            ASCIIColors.yellow(f"Model '{model}' not on {server_name}. Available: {available_models}. Auto-pulling...")
            pull_response = requests.post(
                f"{server_url}/api/pull",
                json={"model": model},
                timeout=_PROXY_TIMEOUT,
            )
            ASCIIColors.yellow(f"{server_name} pull response: {pull_response.status_code} - {pull_response.text[:200]}...")
            pull_response.raise_for_status() # Raise exception for failed pull status

            # 3. Re-check models after successful pull
            available_models = self.get_server_available_models(server_name, server_url)
            config["available_models"] = available_models
            matched_model = self.match_model(model, available_models)

            if matched_model:
                ASCIIColors.green(f"Successfully pulled and matched model '{model}' ({matched_model}) on {server_name}.")
                post_data_dict["model"] = matched_model
                return json.dumps(post_data_dict).encode("utf-8")
            else:
                ASCIIColors.red(f"Model '{model}' still not available after pull on {server_name}. Available: {available_models}.")
                return None # Signal failure for this server

        except Exception as e:
            ASCIIColors.red(f"Failed during model check/pull for '{model}' on {server_name}: {e}")
            # Treat errors during check/pull (connection, pull fail status, etc.) as model unavailable
            return None # Signal failure for this server


    def _attempt_request_on_server(self, server_name, config, path, get_params, post_data, client_ip):
        """
        Attempts a single request to a specific server, handling model checks and logging.
        Returns:
            bool: True if the request was successfully handled (2xx response sent or non-retryable 4xx error sent).
                False if the request failed in a way that warrants trying another server or retrying (5xx, connection error, timeout, model pull fail).
        """
        start_time = time.time()
        load_tracker = config.get("queue")
        queue_size = load_tracker.qsize() if load_tracker else -1
        log_event_prefix = "gen" if path in _GENERATE_PATHS else "default"
        is_generate_path = path in _GENERATE_PATHS
        post_data_dict = {} # Initialize for potential stream check

        # 1. Handle model availability for generate paths
        if is_generate_path:
            updated_post_data = self._handle_model_check_and_pull(server_name, config, post_data)
            if updated_post_data is None:
                # Model check/pull failed, don't proceed with this server
                self.config_manager.update_server_process_time(server_name) # Still count as an attempt
                return False # Signal failure, try next server
            post_data = updated_post_data # Use potentially updated post_data
            post_data_dict = self._decode_post_data(post_data) # Decode again if updated

        # 2. Log initial attempt (if applicable) and prepare request
        if is_generate_path and load_tracker:
            self._log_request_outcome(f"{log_event_prefix}_request", server_name, path, get_params, post_data, client_ip, None, queue_size=queue_size)
            load_tracker.put_nowait(1) # Increment queue count only for generate paths

        response = None
        error = None
        request_handled = False # Flag to indicate if response was sent or error is final

        # 3. Execute the request
        try:
            stream = post_data_dict.get("stream", False) if is_generate_path else False # Check stream only for generate
            response = requests.request(
                self.command,
                config["url"] + path,
                params=get_params,
                data=post_data,
                stream=stream,
                timeout=_PROXY_TIMEOUT,
            )
            response.raise_for_status() # Check for 4xx/5xx HTTP errors

            # Success (2xx)
            self._send_response(response)
            self._log_request_outcome(f"{log_event_prefix}_done", server_name, path, get_params, post_data, client_ip, start_time, response=response, queue_size=queue_size)
            request_handled = True

        # 4. Handle specific exceptions
        except requests.exceptions.HTTPError as e:
            error = e
            response = e.response # Keep response object
            self._log_request_outcome(f"{log_event_prefix}_error", server_name, path, get_params, post_data, client_ip, start_time, response=response, error=error, queue_size=queue_size)
            if 400 <= response.status_code < 500:
                # Client error (4xx) - send response back, do not retry
                error_message = f"{log_event_prefix.capitalize()} request handling failed, GET params={get_params}, POST data={post_data_dict}" # Use decoded dict for logging clarity
                self._send_response(response, error_message)
                request_handled = True
            # else: Server error (5xx) - request_handled remains False, allowing retry

        except requests.exceptions.ConnectionError as e:
            error = e
            ASCIIColors.yellow(f"Could not connect to server {server_name}: {e}")
            self._log_request_outcome("connection_error", server_name, path, get_params, post_data, client_ip, start_time, error=error, queue_size=queue_size)
            # request_handled remains False

        except requests.exceptions.RequestException as e: # Other requests errors (timeout, etc.)
            error = e
            response = getattr(e, 'response', None)
            self._log_request_outcome(f"{log_event_prefix}_error", server_name, path, get_params, post_data, client_ip, start_time, response=response, error=error, queue_size=queue_size)
            # request_handled remains False

        except Exception as e: # Catch unexpected errors during request/handling
            error = e
            ASCIIColors.yellow(f"An unexpected error occurred while routing to {server_name}: {e}")
            self._log_request_outcome("routing_error", server_name, path, get_params, post_data, client_ip, start_time, error=error, queue_size=queue_size)
            # request_handled remains False

        # 5. Finalize attempt
        finally:
            self.config_manager.update_server_process_time(server_name)
            if is_generate_path and load_tracker:
                try:
                    load_tracker.get_nowait() # Ensure queue is decremented if it was incremented
                except Exception as q_e:
                    ASCIIColors.red(f"Error updating queue count for {server_name}: {q_e}")

        return request_handled


    def _handle_pull_broadcast(self, path, get_params, post_data, reachable_servers, client_ip):
        """Handles /api/pull by broadcasting to all reachable servers."""
        ASCIIColors.magenta(f"Broadcasting /api/pull request to {len(reachable_servers)} servers.")
        first_response_sent = False

        for server_name, config in reachable_servers:
            start_time = time.time()
            response = None
            error = None
            try:
                # Direct request, no model check needed for /api/pull itself
                response = requests.request(
                    self.command,
                    config["url"] + path,
                    params=get_params,
                    data=post_data,
                    timeout=_PROXY_TIMEOUT, # Use standard proxy timeout
                )
                # Log success immediately for this server
                self._log_request_outcome("pull_done", server_name, path, get_params, post_data, client_ip, start_time, response=response)
                if not first_response_sent:
                    # Send the first successful response back to the client
                    # Note: subsequent server responses are only logged
                    try:
                        response.raise_for_status() # Check status before sending
                        self._send_response(response)
                        first_response_sent = True
                    except requests.exceptions.HTTPError as http_err:
                        # Log the error for this server even if we don't send it back
                        ASCIIColors.yellow(f"Pull HTTP error from {server_name}: {http_err}")
                        self._log_request_outcome("pull_error", server_name, path, get_params, post_data, client_ip, start_time, response=response, error=http_err)


            except requests.exceptions.RequestException as e:
                error = e
                response = getattr(e, 'response', None)
                ASCIIColors.yellow(f"Error pulling from {server_name}: {e}")
                self._log_request_outcome("pull_error", server_name, path, get_params, post_data, client_ip, start_time, response=response, error=error)
            except Exception as e: # Catch unexpected errors during broadcast to one server
                error = e
                ASCIIColors.red(f"Unexpected error during pull broadcast to {server_name}: {e}")
                self._log_request_outcome("pull_error", server_name, path, get_params, post_data, client_ip, start_time, error=f"Broadcast loop error: {e}")
            finally:
                # Update process time even for pull attempts
                self.config_manager.update_server_process_time(server_name)


        if not first_response_sent:
            # If no server responded successfully (or at all)
            error_message = "Pull request broadcast failed on all reachable servers."
            ASCIIColors.red(error_message)
            self._send_response_code(503, error_message) # Service Unavailable
            self.end_headers()
            # Log final failure for the broadcast operation
            self._log_request_outcome("pull_error", "All", path, get_params, post_data, client_ip, None, error=error_message, access="Denied")

        # Indicate the /api/pull path has been fully handled here
        return True


    # --- Main Method ---

    def _route_request(self, path, get_params, post_data, reachable_servers):
        """
        Routes the request to a proxy server with retries, handling model pulls and specific paths.
        """
        client_ip, _ = self.client_address

        # Handle /api/pull broadcast separately
        if path == "/api/pull":
            self._handle_pull_broadcast(path, get_params, post_data, reachable_servers, client_ip)
            return

        # --- Standard request routing with retries ---
        attempt = 0
        tried_servers_overall = set() # Use set for unique server names

        while attempt < _MAX_RETRIES:
            attempt += 1
            if not reachable_servers:
                ASCIIColors.yellow("No reachable servers available to route request.")
                break # No point retrying if no servers are reachable

            num_servers = len(reachable_servers)
            start_index = (attempt - 1) % num_servers # Simple round-robin start offset per attempt

            for i in range(num_servers):
                server_index = (start_index + i) % num_servers
                server_info = reachable_servers[server_index]
                server_name, config = server_info

                # Avoid retrying the *exact* same server within the same *overall* retry sequence if possible
                # (Though the logic primarily rotates starting point)
                # Simple check: if server_name in tried_servers_overall: continue

                tried_servers_overall.add(server_name)
                self.active_server_name = server_name # Update active server context
                self.active_server_queue_size = config.get("queue", type("obj", (object,), {"qsize": lambda: -1})()).qsize() # Safe queue size access

                ASCIIColors.cyan(f"Attempt {attempt}/{_MAX_RETRIES}: Trying server '{server_name}' for path '{path}'...")

                request_handled = self._attempt_request_on_server(
                    server_name, config, path, get_params, post_data, client_ip
                )

                if request_handled:
                    ASCIIColors.green(f"Request successfully handled by server '{server_name}'.")
                    return # Request succeeded or hit a non-retryable error (e.g., 4xx)

                # If request_handled is False, it means a retryable error occurred (5xx, connection, timeout, model pull fail)
                ASCIIColors.yellow(f"Attempt on server '{server_name}' failed. Trying next available server or retrying...")
                # Loop continues to the next server or the next attempt

        # If loop completes without returning, all attempts failed
        all_tried_servers_str = ', '.join(sorted(list(tried_servers_overall)))
        retry_failed_message = f"Failed to process request on any reachable server after {_MAX_RETRIES} attempts. Tried: [{all_tried_servers_str}]"
        ASCIIColors.red(retry_failed_message)

        # Log final failure
        self._log_request_outcome(
            event="error", server_name="All", path=path, get_params=get_params,
            post_data=post_data, client_ip=client_ip, start_time=None,
            error=f"Failed on all servers after {_MAX_RETRIES} retries: {all_tried_servers_str}",
            access="Denied"
        )

        # Send 503 Service Unavailable
        self._send_response_code(503, retry_failed_message)
        self.end_headers()

    def _handle_request(self):
        """Main handler for incoming requests."""
        start_time = time.time()
        client_ip, _ = self.client_address
        try:
            if not self._handle_security():
                return

            path, get_params, post_data = self._get_request_data()
            reachable_servers = self.get_reachable_servers(path)

            if not reachable_servers:
                not_available_message = f"No reachable Ollama servers available to handle {path}. Servers: {self.config_manager.get_servers()}"
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

