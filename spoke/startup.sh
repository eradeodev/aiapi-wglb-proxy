#!/bin/bash
# Startup script for Spoke node

# Trap SIGTERM and SIGINT to kill background processes on exit
trap 'echo "Caught signal, shutting down..."; jobs -p | xargs -r kill; exit 0' SIGTERM SIGINT

# Set all environment variables
if [ -z "$HUB_ENDPOINT" ]; then
    echo "Error: Hub Endpoint not set"
    exit 1
fi
if [ -z "$HUB_PUB_KEY" ]; then
    echo "Error: HUB Public Key not set"
    exit 1
fi
if [ -z "$DEFAULT_PEER_PRIV_KEY" ]; then
    echo "Error: Default Peer Priv Key not set"
    exit 1
fi

# File to modify for initial config
INITIAL_CONFIG_FILE="initial_config.conf"
CUSTOM_CONFIG_FILE="wg-configs/custom_config.conf"
HUB_INTERNAL_IP="10.123.123.1" # IP address of the Hub internal interface

# Replace placeholders with environment values in the initial config template
if [ -f "$INITIAL_CONFIG_FILE" ]; then
    echo "Modifying $INITIAL_CONFIG_FILE with environment variables."
    # Create a temporary file for sed output to avoid issues with editing the file in place repeatedly if script restarts
    TEMP_INITIAL_CONFIG_FILE=$(mktemp)
    cp "$INITIAL_CONFIG_FILE" "$TEMP_INITIAL_CONFIG_FILE"

    sed -i "s|#HUBENDPOINT#|$HUB_ENDPOINT|g" "$TEMP_INITIAL_CONFIG_FILE"
    sed -i "s|#HUBPUBLICKEY#|$HUB_PUB_KEY|g" "$TEMP_INITIAL_CONFIG_FILE"
    sed -i "s|#DEFAULTPEERPRIV#|$DEFAULT_PEER_PRIV_KEY|g" "$TEMP_INITIAL_CONFIG_FILE"

    # Overwrite the original file with the modified temporary file
    mv "$TEMP_INITIAL_CONFIG_FILE" "$INITIAL_CONFIG_FILE"
else
    echo "Error: $INITIAL_CONFIG_FILE not found!"
    exit 1
fi


function create_new_config {
    echo "Attempting to create a new WireGuard configuration..."
    # Start default wireguard connection, retrying the command until it succeeds
    while true; do
        echo "Bringing up WireGuard interface using $INITIAL_CONFIG_FILE..."
        # Try to bring up the WireGuard interface
        wg-quick up "$INITIAL_CONFIG_FILE"

        # Check if the command was successful
        if [ $? -eq 0 ]; then
            echo "WireGuard connection established successfully using default config."
            break
        else
            echo "Failed to bring up WireGuard using default config. Retrying in 5 seconds..."
            sleep 5
        fi
    done

    # Wait for connection to Hub internal IP via default config
    echo "Waiting for connection to $HUB_INTERNAL_IP via default config..."
    while ! ./check_host.sh "$HUB_INTERNAL_IP"; do
        echo "Waiting for connection to $HUB_INTERNAL_IP via default config..."
        sleep 2
    done
    echo "Connection to Hub established!"

    # Get new config from the Hub
    echo "Requesting new peer configuration from Hub..."
    ./create_new_peer.sh
    if [ $? -ne 0 ]; then
        echo "Error: creating new config failed. Ensure the Hub is accessible and the keys are correct."
        # Kill any background processes we've started (like the WireGuard monitor if it was somehow started)
        jobs -p | xargs -r kill
        exit 1
    fi

    echo "New peer config created ($CUSTOM_CONFIG_FILE) and sent to hub. Waiting 2 seconds for hub processing..."
    sleep 2

    # Tear down default Wireguard config and set up new one
    echo "Tearing down default WireGuard config..."
    wg-quick down "$INITIAL_CONFIG_FILE"

    echo "Bringing up WireGuard interface using $CUSTOM_CONFIG_FILE..."
    wg-quick up "$CUSTOM_CONFIG_FILE"
    if [ $? -ne 0 ]; then
        echo "Error: Failed to bring up WireGuard using $CUSTOM_CONFIG_FILE."
        echo "Removing potentially invalid $CUSTOM_CONFIG_FILE and exiting."
        rm -f "$CUSTOM_CONFIG_FILE"
        jobs -p | xargs -r kill
        exit 1
    fi
    echo "WireGuard connection established successfully using custom config."
}

# Check for custom config, or create one
if [ -f "$CUSTOM_CONFIG_FILE" ]; then
    echo "Custom config found ($CUSTOM_CONFIG_FILE). Attempting to bring up WireGuard."
    wg-quick up "$CUSTOM_CONFIG_FILE"
    if [ $? -ne 0 ]; then
        echo "Failed to bring up WireGuard using $CUSTOM_CONFIG_FILE."
        echo "Removing $CUSTOM_CONFIG_FILE and creating a new one."
        rm -f "$CUSTOM_CONFIG_FILE"
        create_new_config
    else
        echo "WireGuard connection established successfully using custom config."
    fi
else
    echo "Custom config not found. Creating a new one."
    create_new_config
fi

# Initial wait for connection to Hub using custom config
echo "Waiting for initial connection to $HUB_INTERNAL_IP via custom config..."
count=0
while ! ./check_host.sh "$HUB_INTERNAL_IP"; do
    ((count++))
    echo "Waiting for connection to $HUB_INTERNAL_IP via custom config (Attempt $count)..."
    sleep 2 # Wait a bit longer between initial checks
    if [[ $count -ge 6 ]]; then # 6 attempts == 3 minutes at 30 seconds per host check attempt
        echo "Could not establish connection to $HUB_INTERNAL_IP after multiple attempts."
        echo "Tearing down current custom config, removing it, and attempting to retrieve a new one."
        wg-quick down "$CUSTOM_CONFIG_FILE"
        rm -f "$CUSTOM_CONFIG_FILE"
        create_new_config # This function brings up the new config internally
        # After create_new_config, we need to re-enter the waiting loop for the *new* config
        echo "Waiting for connection to $HUB_INTERNAL_IP via newly created custom config..."
        count=0 # Reset counter for the new config wait
    fi
done
echo "Initial connection to Hub established!"

# Periodic connectivity check & auto-recovery for WireGuard
(
while true; do
    if ! ./check_host.sh "$HUB_INTERNAL_IP"; then
        echo "Lost connection to $HUB_INTERNAL_IP. Attempting WireGuard recovery..."
        wg-quick down "$CUSTOM_CONFIG_FILE"
        sleep 2
        echo "Attempting to bring up WireGuard using $CUSTOM_CONFIG_FILE again..."
        wg-quick up "$CUSTOM_CONFIG_FILE"

        # Wait and recheck connection for a few retries
        retry=0
        max_retries=3 # Allow a few retries for wg-quick up
        while ! ./check_host.sh "$HUB_INTERNAL_IP"; do
            ((retry++))
            if [[ $retry -ge $max_retries ]]; then
                echo "WireGuard reconnection attempts failed after $max_retries retries."
                echo "Regenerating config..."
                wg-quick down "$CUSTOM_CONFIG_FILE" # Ensure it's down before removing
                rm -f "$CUSTOM_CONFIG_FILE"
                create_new_config # This function handles bringing the new one up
                # After create_new_config, the outer monitoring loop will continue, and the next ping check should succeed if create_new_config worked.
                break # Exit the inner retry loop
            fi
            echo "Reconnection attempt $retry failed, $CUSTOM_CONFIG_FILE might not be working. Retrying wg-quick up..."
            sleep 2
            wg-quick up "$CUSTOM_CONFIG_FILE" # Try bringing it up again
        done

        if ./check_host.sh "$HUB_INTERNAL_IP"; then
             echo "Connection re-established!"
        fi # If connection was not re-established, create_new_config handled the next step
    fi
    sleep 10 # Check every 10 seconds
done
) &
WG_MONITOR_PID=$! # Capture PID of the WireGuard monitor

# Variables to store server PIDs and memory info
declare -A server_pids # Associative array to store PIDs by endpoint type
declare -A server_ports # Associative array to store ports by endpoint type
declare -A server_configs # Associative array to store config details by endpoint type

completions_pid=0
completion_memory_needed_mib=0
completions_model=""
completions_task=""
completions_port=""
completions_extra_args=""

# Function to get total GPU memory in MiB for a specific GPU ID (default 0)
get_total_gpu_memory_mib() {
    local gpu_id="${1:-0}"
    # Use stderr redirection just in case, though nvidia-smi output is usually stdout
    nvidia-smi --query-gpu=memory.total --format=csv,noheader --id="$gpu_id" 2>/dev/null | awk -F' ' '{print $1}'
}

# Returns something like "7.5"
get_gpu_compute_cap() {
    local gpu_id="${1:-0}"
    nvidia-smi --query-gpu=compute_cap --format=csv,noheader --id="$gpu_id" 2>/dev/null | tr -d ' '
}

# Function to start vllm server
# Note: This function starts the server in the background and *returns* immediately.
# The caller is responsible for capturing the PID if needed.
start_vllm_server_bg() {
    local model="$1"
    local task="$2"
    local port="$3"
    local memory_fraction="$4"
    local extra_args="$5"
    local gpu_id="${6:-0}" # Allow specifying GPU ID

    echo "Starting vllm serve for $task on port $port (GPU $gpu_id) with memory fraction $memory_fraction"

    local current_extra_args="$extra_args"

    # Detect compute capability for the specified GPU
    local compute_cap
    compute_cap=$(get_gpu_compute_cap "$gpu_id")
    if [ -z "$compute_cap" ]; then
        echo "Warning: Could not detect GPU compute capability for GPU ID $gpu_id. Proceeding without --dtype check."
    else
        # If <8.0, force float16
        # awk requires quotes around the script and comparison is safe with float values
        if awk "BEGIN{exit !($compute_cap < 8.0)}"; then
            echo "GPU compute capability $compute_cap < 8.0, adding --dtype=half"
            current_extra_args+=" --dtype=half"
        fi
    fi

    # Start vllm serve in the background
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True vllm serve $model --task "$task" --host 0.0.0.0 --port "$port" --gpu-memory-utilization "$memory_fraction" $current_extra_args &
    # The PID is captured by the caller using $!
}

# Function to start gunicorn server
start_gunicorn_server_bg() {
    local port="$1"
    echo "Starting gunicorn serve for chunking on port $port"
    # Ensure we are in the correct directory before starting gunicorn
    (
        cd /app/chunker_server || { echo "Error: Could not change directory to /app/chunker_server"; exit 1; }
        # Use exec here if this was the *only* process in this background job,
        # but we want it as a background job relative to the main script.
        # Running it with conda run in a subshell `(...)` keeps it contained.
        /root/miniconda3/bin/conda run -n py312 gunicorn --log-level debug --enable-stdio-inheritance --bind 0.0.0.0:$port app:app --workers "$CHUNKER_WORKERS" --access-logfile /proc/1/fd/1 --error-logfile /proc/1/fd/2
    ) &
    # The PID is captured by the caller using $!
}

# Check if servers should be enabled
if [[ -n "$ENABLED_FOR_REQUESTS" ]]; then
    echo "ENABLED_FOR_REQUESTS is set: $ENABLED_FOR_REQUESTS. Attempting to start servers."
    IFS=',' read -ra enabled_endpoints <<< "$ENABLED_FOR_REQUESTS"

    total_gpu_memory_mib=$(get_total_gpu_memory_mib)
    if [ -z "$total_gpu_memory_mib" ]; then
        echo "Warning: Could not detect total GPU memory using nvidia-smi. Skipping vLLM server startup."
        total_gpu_memory_mib=0 # Set to 0 to prevent vLLM startup
    else
        echo "Total GPU memory detected: ${total_gpu_memory_mib}MiB"
    fi
    # Initialize remaining_memory_mib with total available
    remaining_memory_mib="$total_gpu_memory_mib"

    # Default initial memory needed for completions (this will dynamically increase on restarts)
    # This value is the STARTING point, the actual allocated memory is tracked in completion_memory_needed_mib
    initial_completion_memory_mib=8192

    # === Prioritize and attempt to start /v1/chat/completions ===
    for endpoint_config in "${enabled_endpoints[@]}"; do
        if [[ "$endpoint_config" == *"/v1/chat/completions"* ]]; then
            completions_port=$(echo "$endpoint_config" | cut -d':' -f2 | cut -d'/' -f1)
            echo "Configuration found for completions server on port $completions_port."

            if [[ "$total_gpu_memory_mib" -gt 0 ]]; then
                # Set the initial memory needed for this attempt
                completion_memory_needed_mib="$initial_completion_memory_mib"
                # Check if enough memory is available for the initial allocation + 512MiB free
                if [[ "$remaining_memory_mib" -ge $((completion_memory_needed_mib + 512)) ]]; then
                    completions_model="unsloth/Qwen3-4B-Thinking-2507-unsloth-bnb-4bit"
                    completions_task="generate"
                    # Capture the extra args needed for this specific model
                    completions_extra_args="--tokenizer unsloth/Qwen3-4B-Thinking-2507 --load-format bitsandbytes --quantization bitsandbytes --max_model_len 32768 --max-model-len 32768 --max-num-seqs 1 --max_num_seqs 1 --max-seq-len-to-capture 1024 --enforce-eager --reasoning-parser qwen3 --disable-log-requests"

                    completion_memory_fraction_float=$(awk -v needed="$completion_memory_needed_mib" -v total="$total_gpu_memory_mib" 'BEGIN{printf "%.4f", needed / total}')
                    echo "Attempting to start vllm serve for completions on port $completions_port with fraction $completion_memory_fraction_float (${completion_memory_needed_mib}MiB)..."

                    # Start the server and capture its PID
                    start_vllm_server_bg "$completions_model" "$completions_task" "$completions_port" "$completion_memory_fraction_float" "$completions_extra_args"
                    completions_pid=$!
                    server_pids["completions"]="$completions_pid"
                    server_ports["completions"]="$completions_port"
                    server_configs["completions"]="${completions_model}|${completions_task}|${completions_extra_args}" # Store config for restart

                    # Subtract allocated memory
                    remaining_memory_mib=$((remaining_memory_mib - completion_memory_needed_mib))
                    echo "Completions server started with PID $completions_pid. Remaining GPU memory: ${remaining_memory_mib}MiB"
                else
                    echo "Not enough GPU memory to start completions server with initial allocation (${completion_memory_needed_mib}MiB needed + 512MiB free required, ${remaining_memory_mib}MiB available)."
                    # Kill all background jobs (WireGuard monitor, other servers) and exit
                    echo "Killing background processes and exiting."
                    jobs -p | xargs -r kill
                    exit 1 # Exit the script due to unrecoverable error
                fi
            else
                echo "GPU not detected or total memory is 0. Skipping completions server startup."
            fi
            break # Prioritize and attempt to start only one completions server config
        fi
    done

    # === Then attempt to start /v1/embeddings if enough memory remains ===
    for endpoint_config in "${enabled_endpoints[@]}"; do
        if [[ "$endpoint_config" == *"/v1/embeddings"* ]]; then
            embeddings_port=$(echo "$endpoint_config" | cut -d':' -f2 | cut -d'/' -f1)
            echo "Configuration found for embeddings server on port $embeddings_port."
            #[ -f /app/models/Qwen3-Embedding-0.6B-Q8_0.gguf ] || wget -O /app/models/Qwen3-Embedding-0.6B-Q8_0.gguf https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/Qwen3-Embedding-0.6B-Q8_0.gguf

            embedding_memory_needed_mib=4608 # Fixed allocation for embeddings
            if [[ "$remaining_memory_mib" -ge $((embedding_memory_needed_mib + 512)) ]]; then # Check against remaining + leave 512 free
                embeddings_model="AXERA-TECH/Qwen3-Embedding-0.6B-GPTQ-Int8"
                embeddings_task="embed"
                embeddings_extra_args="--max_model_len 32768 --max-model-len 32768 --max-num-seqs 1 --max_num_seqs 1 --max-seq-len-to-capture 1024 --enforce-eager --disable-log-requests"

                embedding_memory_fraction_float=$(awk -v needed="$embedding_memory_needed_mib" -v total="$total_gpu_memory_mib" 'BEGIN{printf "%.4f", needed / total}')
                echo "Attempting to start vllm serve for embeddings on port $embeddings_port with fraction $embedding_memory_fraction_float (${embedding_memory_needed_mib}MiB)..."

                start_vllm_server_bg $embeddings_model "$embeddings_task" "$embeddings_port" "$embedding_memory_fraction_float" "$embeddings_extra_args"
                server_pids["embeddings"]=$!
                server_ports["embeddings"]="$embeddings_port"
                server_configs["embeddings"]="${embeddings_model}|${embeddings_task}|${embeddings_extra_args}" # Store config

                remaining_memory_mib=$((remaining_memory_mib - embedding_memory_needed_mib))
                echo "Embeddings server started with PID ${server_pids["embeddings"]}. Remaining GPU memory: ${remaining_memory_mib}MiB"
            else
                echo "Not enough remaining GPU memory to start embeddings server (${embedding_memory_needed_mib}MiB needed + 512MiB free required, ${remaining_memory_mib}MiB available)."
            fi
            break # Start only one embeddings server config
        fi
    done

    # === Start chunker servers regardless of vllm status ===
    # Note: Chunker does not use GPU memory in this setup, so no memory check needed
    for endpoint_config in "${enabled_endpoints[@]}"; do
        if [[ "$endpoint_config" == *"/api/chunk"* ]]; then
            chunker_port=$(echo "$endpoint_config" | cut -d':' -f2 | cut -d'/' -f1)
            echo "Configuration found for chunker server on port $chunker_port."
            start_gunicorn_server_bg "$chunker_port"
            server_pids["chunker"]=$! # Store the last PID launched by the background job
            server_ports["chunker"]="$chunker_port"
            echo "Chunker server started (PID ${server_pids["chunker"]})."
            # Note: We don't break here in case multiple chunker endpoints are configured, though typically only one is needed.
        fi
    done

fi # End of ENABLED_FOR_REQUESTS block

# --- Main monitoring loop ---
# This loop keeps the script alive and monitors the completions server.
echo "Starting main monitoring loop."
while true; do
    # Check if the completions server PID exists and the process is running
    # We only monitor completions as per the specific requirement
    if [ "$completions_pid" -gt 0 ]; then # Only monitor if a completions server was attempted
        if ! kill -0 "$completions_pid" > /dev/null 2>&1; then
            echo "Completions server process (PID $completions_pid) has exited or is not running."

            # Add the previously allocated memory back to the remaining pool
            # This assumes completion_memory_needed_mib holds the memory allocated for the *last failed* process
            echo "Adding ${completion_memory_needed_mib}MiB back to remaining pool."
            remaining_memory_mib=$((remaining_memory_mib + completion_memory_needed_mib))
            echo "Remaining GPU memory is now: ${remaining_memory_mib}MiB"

            # Attempt to restart with increased memory
            memory_increment=0
            minimum_free_after_alloc=256 # Minimum memory to leave free after allocation

            # Calculate potential memory needed with a 512 MiB increment
            attempt_needed_mib_512=$((completion_memory_needed_mib + 512))
            # Check if remaining memory is sufficient for this attempt PLUS the minimum free
            if [[ "$remaining_memory_mib" -ge $((attempt_needed_mib_512 + minimum_free_after_alloc)) ]]; then
                 memory_increment=512
                 echo "Attempting restart with +512 MiB increment."
            # If 512 MiB increment + 512 free is not possible
            else
                 echo "Not enough memory for +512 MiB increment. Attempting to use maximum available memory."
                 # Calculate the maximum memory we *can* allocate while leaving minimum_free_after_alloc
                 max_possible_allocation=$((remaining_memory_mib - minimum_free_after_alloc))

                 # Check if this maximum possible allocation is larger than the current allocation
                 if [[ "$max_possible_allocation" -gt "$completion_memory_needed_mib" ]]; then
                     memory_increment=$((max_possible_allocation - completion_memory_needed_mib))
                     echo "Attempting restart using ${memory_increment} MiB additional memory (total target ${max_possible_allocation} MiB)."
                     # The rest of the script after this block will use the calculated memory_increment
                 else
                     # Even allocating max possible doesn't give a positive increment or leaves less than minimum_free_after_alloc
                     echo "ERROR: Failed to restart completions server."
                     echo "Cannot allocate more memory while leaving ${minimum_free_after_alloc}MiB free."
                     echo "Current required memory target: ${completion_memory_needed_mib}MiB"
                     echo "Maximum possible allocation while leaving ${minimum_free_after_alloc}MiB free: ${max_possible_allocation}MiB"
                     echo "Available memory: ${remaining_memory_mib}MiB"

                     # Kill all background jobs (WireGuard monitor, other servers) and exit
                     echo "Killing background processes and exiting."
                     jobs -p | xargs -r kill
                     exit 1 # Exit the script due to unrecoverable error
                 fi
            fi

            # If we reached here, memory_increment is either 1024 or 512
            completion_memory_needed_mib=$((completion_memory_needed_mib + memory_increment))
            echo "New memory allocation target for completions: ${completion_memory_needed_mib}MiB"

            # Recalculate fraction based on TOTAL GPU memory and NEW needed memory
            # Ensure total_gpu_memory_mib is not zero to avoid division by zero
            if [[ "$total_gpu_memory_mib" -gt 0 ]]; then
                completion_memory_fraction_float=$(awk -v needed="$completion_memory_needed_mib" -v total="$total_gpu_memory_mib" 'BEGIN{printf "%.4f", needed / total}')
                echo "Restarting vllm serve for completions on port $completions_port with fraction $completion_memory_fraction_float"

                # Extract stored config details
                IFS='|' read -r completions_model completions_task completions_extra_args <<< "${server_configs["completions"]}"

                # Start the server again and capture the new PID
                start_vllm_server_bg "$completions_model" "$completions_task" "$completions_port" "$completion_memory_fraction_float" "$completions_extra_args"
                completions_pid=$! # Capture the NEW PID
                server_pids["completions"]="$completions_pid" # Update the stored PID

                # Subtract the NEW allocated memory
                remaining_memory_mib=$((remaining_memory_mib - completion_memory_needed_mib))
                echo "Completions server restarted with PID $completions_pid. Remaining GPU memory: ${remaining_memory_mib}MiB"
            else
                 echo "Cannot restart vLLM server: Total GPU memory is 0 or not detected."
                 echo "Killing background processes and exiting."
                 jobs -p | xargs -r kill
                 exit 1 # Exit if GPU disappeared or was never found
            fi
        fi
    fi

    # Sleep before checking again
    sleep 10
done

# The script will stay in the while loop above.
# The trap handlers will catch signals (like Ctrl+C or kill) to exit cleanly.
# If the while loop ever exits unexpectedly (e.g., fatal error not caught), the script would finish.
# We added an exit 1 within the memory failure branch, which is the primary exit path for errors now.