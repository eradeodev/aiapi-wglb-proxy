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

    # --- Instance variables for request context ---
    # These will be set by _get_request_data
    request_path = None
    request_get_params = None
    request_post_data = None
    user = 'unknown' # Initialize user
    active_server_name = 'unset_server'
    active_server_queue_size = -1


    def _normalize_model_name(self, name):
        """Removes ':latest' suffix from a model name if present."""
        if name and name.endswith(':latest'):
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
                    enabled = config.get("enabled_for_requests", [])
                    ASCIIColors.yellow(
                        f"Server {server_name} enabled_for_requests = {enabled}"
                    )
                    if not enabled or path in enabled:
                        available_models = self.get_server_available_models(server_name, config["url"])
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
        """Queries the server for its available models via a GET request to /api/tags."""
        # --- Logging kept manual as it's an internal task ---
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
        """Sends only a status code and headers. Logs manually due to early exit context."""
        server_name = getattr(self, "active_server_name", "unset_server")
        queue_size = getattr(self, 'active_server_queue_size', 'N/A')
        server_log_info = f"{server_name}- Queue Size {queue_size}" if server_name != "unset_server" else "unset_server"
        # --- Logging kept manual due to potential lack of full request context ---
        self.request_logger.log(
            event="response_sent_code_only", # Differentiate event slightly
            user=getattr(self, 'user', 'unknown'),
            ip_address=self.client_address[0],
            access="Authorized" if getattr(self, 'user', 'unknown') != 'unknown' else 'Denied',
            server=server_log_info,
            nb_queued_requests_on_server=queue_size if isinstance(queue_size, int) else -1,
            response_status=response_code,
            message=message,
            request_path=getattr(self, 'request_path', self.path), # Try to get path if available
        )
        self.send_response(response_code, message)

    def _send_response(self, response, message=""):
        """Sends a full response based on the requests.Response object. Logging is handled by _log_request_outcome."""

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
        except Exception as e:
             # Log error during response writing if needed
             ASCIIColors.red(f"Error writing response body: {e}")
             self._log_request_outcome(
                 event="response_write_error",
                 server_name=getattr(self, "active_server_name", "unset_server"),
                 path=getattr(self, 'request_path', 'unknown'),
                 get_params=getattr(self, 'request_get_params', {}),
                 post_data=getattr(self, 'request_post_data', b''),
                 client_ip=self.client_address[0],
                 start_time=None, # No start time available here
                 response=response, # Pass the original response
                 error=f"Error writing response body: {e}",
                 queue_size=getattr(self, 'active_server_queue_size', -1),
                 access="Authorized" if getattr(self, 'user', 'unknown') != 'unknown' else 'Denied' # Use established access
             )

    def log_message(self, format, *args):
        # Suppress default BaseHTTPRequestHandler logging
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
            self.user = user # Store validated or attempted user
            client_ip, _ = self.client_address
            if authorized:
                return True
            else:
                ASCIIColors.red(f"User '{user}' is not authorized")
                # --- Logging kept manual due to early exit context ---
                self.request_logger.log(
                    event="rejected",
                    user=user,
                    ip_address=client_ip,
                    access="Denied",
                    server="None",
                    nb_queued_requests_on_server=-1,
                    error="Authentication failed",
                    request_path=urlparse(self.path).path # Get path for context
                )
                self._send_response_code(403, f"User '{user}' is not authorized")
                self.end_headers()
                return False

    def _get_request_data(self):
        """
        Extracts path, GET parameters, and POST data from the request
        and stores them as instance attributes.
        """
        url = urlparse(self.path)
        self.request_path = url.path
        self.request_get_params = parse_qs(url.query) or {}
        self.request_post_data = b'' # Initialize as bytes
        if self.command in ["POST", "PUT"]:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 0:
                self.request_post_data = self.rfile.read(content_length)
        # No return needed, data stored on self

    def match_model(self, model, models):
        """Matches requested model against available models (exact, normalized, substring)."""
        matched_model = next((m for m in models if m == model), None)
        normalized_model = self._normalize_model_name(model)
        if not matched_model:
            matched_model = next((m for m in models if self._normalize_model_name(m) == normalized_model), None)
        if not matched_model:
            matched_model = next((m for m in models if normalized_model in self._normalize_model_name(m)), None)
        return matched_model

    # --- Helper Methods ---

    def _log_request_outcome(self, event, server_name, path, get_params, post_data,
                            client_ip, start_time, response=None, error=None,
                            queue_size=-1, access="Authorized"):
        """Centralized logging for request outcomes."""
        duration = time.time() - start_time if start_time else None
        # Ensure post_data is bytes before decoding attempt
        post_body_str = post_data.decode('utf-8', errors='ignore') if isinstance(post_data, bytes) else str(post_data)
        status = getattr(response, 'status_code', None)

        log_data = {
            "event": event,
            "user": self.user, # Use instance user attribute
            "ip_address": client_ip,
            "access": access,
            "server": server_name,
            "nb_queued_requests_on_server": queue_size,
            "request_path": path,
            "request_params": get_params,
            "request_body": post_body_str, # Log decoded string
        }
        if status is not None:
            log_data["response_status"] = status
        if error is not None:
            if isinstance(error, requests.exceptions.HTTPError) and error.response is not None:
                log_data["error"] = f"HTTP error {error.response.status_code}: {error}"
                log_data["response_status"] = error.response.status_code
            else:
                log_data["error"] = str(error)

        if duration is not None:
            log_data["duration"] = round(duration, 4) # Add duration if available

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
        if isinstance(post_data, dict):
            return post_data
        if post_data is None:
            return {}
        if isinstance(post_data, str):
            try:
                return json.loads(post_data)
            except json.JSONDecodeError:
                ASCIIColors.yellow("Could not decode string post data as JSON.")
                return {}
        ASCIIColors.yellow(f"Unexpected post_data type: {type(post_data)}")
        return {}

    def _handle_model_check_and_pull(self, server_name, config, post_data):
        """
        Checks model availability, attempts auto-pull if missing.
        Returns potentially updated post_data (bytes), or None if model unavailable/pull failed.
        """
        post_data_dict = self._decode_post_data(post_data)
        model = post_data_dict.get("model")

        if not model:
            return post_data # Proceed if no model specified

        server_url = config["url"]
        try:
            available_models = self.get_server_available_models(server_name, server_url)
            config["available_models"] = available_models # Update cache

            matched_model = self.match_model(model, available_models)

            if matched_model:
                ASCIIColors.yellow(f"{server_name} found matched model for '{model}': '{matched_model}'")
                if model != matched_model:
                    post_data_dict["model"] = matched_model
                    return json.dumps(post_data_dict).encode("utf-8")
                return post_data # Exact or already matched

            ASCIIColors.yellow(f"Model '{model}' not on {server_name}. Available: {available_models}. Auto-pulling...")
            pull_response = requests.post(
                f"{server_url}/api/pull",
                json={"model": model},
                timeout=_PROXY_TIMEOUT,
            )
            ASCIIColors.yellow(f"{server_name} pull response: {pull_response.status_code} - {pull_response.text[:200]}...")
            pull_response.raise_for_status()

            available_models = self.get_server_available_models(server_name, server_url)
            config["available_models"] = available_models
            matched_model = self.match_model(model, available_models)

            if matched_model:
                ASCIIColors.green(f"Successfully pulled and matched model '{model}' ({matched_model}) on {server_name}.")
                post_data_dict["model"] = matched_model
                return json.dumps(post_data_dict).encode("utf-8")
            else:
                ASCIIColors.red(f"Model '{model}' still not available after pull on {server_name}. Available: {available_models}.")
                return None # Signal failure

        except Exception as e:
            ASCIIColors.red(f"Failed during model check/pull for '{model}' on {server_name}: {e}")
            return None # Signal failure

    def _attempt_request_on_server(self, server_name, config, path, get_params, post_data, client_ip):
        """
        Attempts a single request to a specific server. Returns True if handled, False if retry needed.
        Uses _log_request_outcome for logging results.
        """
        start_time = time.time()
        load_tracker = config.get("queue")
        queue_size = load_tracker.qsize() if load_tracker else -1
        log_event_prefix = "gen" if path in _GENERATE_PATHS else "default"
        is_generate_path = path in _GENERATE_PATHS
        post_data_dict = {}
        current_post_data = post_data # Keep track of potentially modified post data

        # 1. Handle model availability for generate paths
        if is_generate_path:
            updated_post_data = self._handle_model_check_and_pull(server_name, config, current_post_data)
            if updated_post_data is None:
                self.config_manager.update_server_process_time(server_name)
                self._log_request_outcome("model_pull_fail", server_name, path, get_params, post_data, client_ip, start_time, error="Model check/pull failed", queue_size=queue_size)
                return False # Signal failure, try next server
            current_post_data = updated_post_data # Use potentially updated data
            post_data_dict = self._decode_post_data(current_post_data) # Decode again if updated

        # 2. Log initial attempt (if applicable) and prepare request
        if is_generate_path and load_tracker:
            # Log the *actual* post data being sent (potentially updated)
            self._log_request_outcome(f"{log_event_prefix}_request", server_name, path, get_params, current_post_data, client_ip, None, queue_size=queue_size)
            load_tracker.put_nowait(1)

        response = None
        error = None
        request_handled = False # Flag indicates response sent or non-retryable error

        # 3. Execute the request
        try:
            stream = post_data_dict.get("stream", False) if is_generate_path else False
            response = requests.request(
                self.command,
                config["url"] + path,
                params=get_params,
                data=current_post_data, # Use current (potentially updated) post data
                stream=stream,
                timeout=_PROXY_TIMEOUT,
            )
            response.raise_for_status()

            # Success (2xx)
            self._send_response(response) # Send response
            self.active_server_name = server_name # Record successful server
            self.active_server_queue_size = queue_size # Record queue size at time of success
            self._log_request_outcome(f"{log_event_prefix}_done", server_name, path, get_params, current_post_data, client_ip, start_time, response=response, queue_size=queue_size)
            request_handled = True

        # 4. Handle specific exceptions
        except requests.exceptions.HTTPError as e:
            error = e
            response = e.response
            # Log using current_post_data for accuracy
            self._log_request_outcome(f"{log_event_prefix}_error", server_name, path, get_params, current_post_data, client_ip, start_time, response=response, error=error, queue_size=queue_size)
            if 400 <= response.status_code < 500:
                error_message = f"{log_event_prefix.capitalize()} request handling failed on {server_name}." # Simpler message
                self._send_response(response, error_message) # Send actual error response
                request_handled = True
            # 5xx errors mean request_handled remains False -> retry

        except requests.exceptions.ConnectionError as e:
            error = e
            ASCIIColors.yellow(f"Could not connect to server {server_name}: {e}")
            self._log_request_outcome("connection_error", server_name, path, get_params, current_post_data, client_ip, start_time, error=error, queue_size=queue_size)

        except requests.exceptions.RequestException as e:
            error = e
            response = getattr(e, 'response', None)
            self._log_request_outcome(f"{log_event_prefix}_error", server_name, path, get_params, current_post_data, client_ip, start_time, response=response, error=error, queue_size=queue_size)

        except Exception as e:
            error = e
            ASCIIColors.yellow(f"An unexpected error occurred while routing to {server_name}: {e}")
            self._log_request_outcome("routing_error", server_name, path, get_params, current_post_data, client_ip, start_time, error=error, queue_size=queue_size)

        # 5. Finalize attempt
        finally:
            self.config_manager.update_server_process_time(server_name)
            if is_generate_path and load_tracker:
                try:
                    load_tracker.get_nowait()
                except Exception as q_e:
                    ASCIIColors.red(f"Error updating queue count for {server_name}: {q_e}")

        return request_handled

    def _handle_pull_broadcast(self, path, get_params, post_data, reachable_servers, client_ip):
        """Handles /api/pull by broadcasting. Uses _log_request_outcome."""
        ASCIIColors.magenta(f"Broadcasting /api/pull request to {len(reachable_servers)} servers.")
        first_response_sent = False
        overall_start_time = time.time() # For final log if needed

        for server_name, config in reachable_servers:
            start_time = time.time()
            response = None
            error = None
            try:
                response = requests.request(
                    self.command,
                    config["url"] + path,
                    params=get_params,
                    data=post_data,
                    timeout=_PROXY_TIMEOUT,
                )
                # Log immediately, check status before sending
                self._log_request_outcome("pull_attempt_done", server_name, path, get_params, post_data, client_ip, start_time, response=response)

                if not first_response_sent:
                    try:
                        response.raise_for_status() # Check status ONLY for the one we send back
                        self._send_response(response) # Send first success back
                        first_response_sent = True
                        self.active_server_name = server_name # Mark which server gave the primary response
                        # Log that this was the primary success
                        self._log_request_outcome("pull_primary_success", server_name, path, get_params, post_data, client_ip, start_time, response=response)

                    except requests.exceptions.HTTPError as http_err:
                        # Log the error for this server even if we don't send it back yet
                        ASCIIColors.yellow(f"Pull HTTP error from {server_name} (not sent to client unless first): {http_err}")
                        # Log separately that this specific server failed, but wasn't the primary failure yet
                        self._log_request_outcome("pull_attempt_fail", server_name, path, get_params, post_data, client_ip, start_time, response=response, error=http_err)

            except requests.exceptions.RequestException as e:
                error = e
                response = getattr(e, 'response', None)
                ASCIIColors.yellow(f"Error pulling from {server_name}: {e}")
                self._log_request_outcome("pull_error", server_name, path, get_params, post_data, client_ip, start_time, response=response, error=error)
            except Exception as e:
                error = e
                ASCIIColors.red(f"Unexpected error during pull broadcast to {server_name}: {e}")
                self._log_request_outcome("pull_error", server_name, path, get_params, post_data, client_ip, start_time, error=f"Broadcast loop error: {e}")
            finally:
                self.config_manager.update_server_process_time(server_name)


        if not first_response_sent:
            error_message = "Pull request broadcast failed or returned errors on all reachable servers."
            ASCIIColors.red(error_message)
            self._send_response_code(503, error_message)
            self.end_headers()
            # --- Use _log_request_outcome for final broadcast failure ---
            self._log_request_outcome(
                event="pull_broadcast_failed",
                server_name="All",
                path=path,
                get_params=get_params,
                post_data=post_data,
                client_ip=client_ip,
                start_time=overall_start_time, # Log duration of whole broadcast attempt
                error=error_message,
                access="Denied" # Denied because no server could fulfill it
            )
        # Indicate the /api/pull path has been fully handled here
        return True # Signal handled regardless of success/fail

    # --- Main Method ---

    def _route_request(self, path, get_params, post_data, reachable_servers):
        """
        Routes the request to a proxy server with retries.
        Uses _log_request_outcome for final failure logging.
        """
        client_ip, _ = self.client_address

        # Handle /api/pull broadcast separately
        if path == "/api/pull":
            # Pass instance attributes for consistency if needed, though pull doesn't use them internally much
            self._handle_pull_broadcast(self.request_path, self.request_get_params, self.request_post_data, reachable_servers, client_ip)
            return

        # --- Standard request routing with retries ---
        attempt = 0
        tried_servers_overall = set()
        overall_start_time = time.time() # Time the whole routing process

        while attempt < _MAX_RETRIES:
            attempt += 1
            current_servers = self.get_reachable_servers(path) # Re-check reachability each attempt? Or use initial list? Let's use initial for now.
            if not current_servers: # Use current_servers instead of reachable_servers if re-checking
                 ASCIIColors.yellow(f"No reachable servers available on attempt {attempt}.")
                 break # Exit retry loop if no servers left

            num_servers = len(current_servers)
            start_index = (attempt - 1) % num_servers

            for i in range(num_servers):
                server_index = (start_index + i) % num_servers
                server_info = current_servers[server_index] # Use current list
                server_name, config = server_info

                if server_name in tried_servers_overall and num_servers > 1:
                     # Simple optimization: If we already tried this server in a *previous* full attempt,
                     # and there are other servers available in *this* attempt's list, skip it for now.
                     # This helps prevent immediately retrying a known-bad server if others exist.
                     # Note: Doesn't prevent trying it again if it's the *only* one left or in a later attempt.
                     # ASCIIColors.grey(f"Attempt {attempt}: Skipping server '{server_name}' (already tried in previous attempt).")
                     continue # Try next server in this attempt first

                tried_servers_overall.add(server_name)
                # active_server context is set within _attempt_request_on_server on success

                ASCIIColors.cyan(f"Attempt {attempt}/{_MAX_RETRIES}: Trying server '{server_name}' for path '{path}'...")

                request_handled = self._attempt_request_on_server(
                    server_name, config, path, get_params, post_data, client_ip
                )

                if request_handled:
                    ASCIIColors.green(f"Request successfully handled by server '{server_name}'.")
                    # Success logged within _attempt_request_on_server
                    return # Exit routing function

                # If request_handled is False, log attempt failure and continue loop
                ASCIIColors.yellow(f"Attempt on server '{server_name}' failed. Trying next available server or retrying...")
                # Loop continues

        # If loop completes without returning, all attempts failed
        all_tried_servers_str = ', '.join(sorted(list(tried_servers_overall)))
        retry_failed_message = f"Failed to process request on any reachable server after {_MAX_RETRIES} attempts. Tried: [{all_tried_servers_str}]"
        ASCIIColors.red(retry_failed_message)

        # --- Use _log_request_outcome for final routing failure ---
        self._log_request_outcome(
            event="routing_failed_all_attempts",
            server_name="All", # Indicates failure across all tried servers
            path=path,
            get_params=get_params,
            post_data=post_data,
            client_ip=client_ip,
            start_time=overall_start_time, # Log duration for the entire routing attempt
            error=retry_failed_message,
            access="Denied" # Ultimately denied access to service
        )

        # Send 503 Service Unavailable
        self._send_response_code(503, retry_failed_message)
        self.end_headers()


    def _handle_request(self):
        """Main handler for incoming requests."""
        start_time = time.time()
        client_ip, _ = self.client_address
        access_status = "Denied" # Default access status for logging

        try:
            # Security check - sets self.user and potentially exits
            if not self._handle_security():
                # Logging is handled within _handle_security for rejection
                return # Exit if security failed

            # If security passed
            access_status = "Authorized" # Update access status for subsequent logs

            # Parse request data and store on self
            self._get_request_data()

            # Find reachable servers for the specific path
            reachable_servers = self.get_reachable_servers(self.request_path)

            if not reachable_servers:
                not_available_message = f"No reachable Ollama servers available to handle {self.request_path}."
                self._send_response_code(503, not_available_message)
                self.end_headers()
                ASCIIColors.red(not_available_message)
                # --- Logging kept manual for this specific early exit ---
                self.request_logger.log(
                    event="error_no_servers",
                    user=self.user,
                    ip_address=client_ip,
                    access=access_status, # Should be Authorized if past security
                    server="None",
                    nb_queued_requests_on_server=-1,
                    error="No reachable Ollama servers",
                    request_path=self.request_path,
                    request_params=self.request_get_params,
                    request_body=self.request_post_data.decode('utf-8', errors='ignore') if isinstance(self.request_post_data, bytes) else str(self.request_post_data),
                    duration = time.time() - start_time # Log duration up to this point
                )
                return

            # Route the request using instance attributes
            self._route_request(
                self.request_path,
                self.request_get_params,
                self.request_post_data,
                reachable_servers
            )
            # Outcome (success or final failure) is logged within _route_request or _attempt_request_on_server

        except Exception as e:
            # Catch-all for unexpected errors during request handling (after security/parsing)
            ASCIIColors.red(f"An unexpected error occurred while handling the request: {e}")
            traceback.print_exc()
            try:
                # Attempt to send 500
                self._send_response_code(500, "Internal Server Error")
                self.end_headers()
            except Exception as send_err:
                ASCIIColors.red(f"Failed to send error response to client: {send_err}")

            # --- Use _log_request_outcome for unexpected errors ---
            self._log_request_outcome(
                event="unexpected_handler_error",
                server_name=getattr(self, 'active_server_name', 'None'), # Log last known server if available
                path=getattr(self, 'request_path', 'unknown'),
                get_params=getattr(self, 'request_get_params', {}),
                post_data=getattr(self, 'request_post_data', b''),
                client_ip=client_ip,
                start_time=start_time, # Log duration until error
                error=f"Unexpected handler error: {e}",
                queue_size=getattr(self, 'active_server_queue_size', -1),
                access=access_status # Log access status determined earlier
            )