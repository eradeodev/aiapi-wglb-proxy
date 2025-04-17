#!/bin/bash
# Startup script for Spoke node

# Set all environment variables
if [ -z "$HUB_ENDPOINT" ]; then
    echo "Hub Endpoint not set"
    exit 1
fi
if [ -z "$HUB_PUB_KEY" ]; then
    echo "HUB Public Key not set"
    exit 1
fi
if [ -z "$DEFAULT_PEER_PRIV_KEY" ]; then
    echo "Default Peer Priv Key not set"
    exit 1
fi

# File to modify
FILE="initial_config.conf"

# Replace placeholders with environment values
sed -i "s|#HUBENDPOINT#|$HUB_ENDPOINT|g" "$FILE"
sed -i "s|#HUBPUBLICKEY#|$HUB_PUB_KEY|g" "$FILE"
sed -i "s|#DEFAULTPEERPRIV#|$DEFAULT_PEER_PRIV_KEY|g" "$FILE"

function create_new_config {
    # Start default wireguard connection, retrying the command until it succeeds
    while true; do
        # Try to bring up the WireGuard interface
        wg-quick up ./initial_config.conf

        # Check if the command was successful
        if [ $? -eq 0 ]; then
            echo "WireGuard connection established successfully."
            break
        else
            echo "Failed to bring up WireGuard. Retrying in 5 seconds..."
            sleep 5
        fi
    done

    # Wait for connection to Hub
    echo "Waiting for connection to 10.123.123.1 via default config..."
    while ! ./check_host.sh 10.123.123.1; do
        echo "Waiting for connection to 10.123.123.1 via default config..."
        sleep 2
    done
    echo "Connection established!"

    # Get new config
    ./create_new_peer.sh
    if [ $? -ne 0 ]; then
        echo "creating config failed."
        # Kill any background processes we've started
        jobs -p | xargs -r kill
        exit 1
    fi

    echo "New peer config created and sent to hub. Waiting 2 seconds for hub processing..."
    sleep 2
    # Tear down default Wireguard config and set up new one
    wg-quick down ./initial_config.conf
    wg-quick up ./wg-configs/custom_config.conf
}

# Check for custom config, or create one
if [ -f "wg-configs/custom_config.conf" ]; then
    wg-quick up ./wg-configs/custom_config.conf
else
    create_new_config
fi

echo "Waiting for connection to 10.123.123.1 via custom config..."
count=0
while ! ./check_host.sh 10.123.123.1; do
    ((count++))
    echo "Waiting for connection to 10.123.123.1 via custom config..."
    sleep 2
    if [[ $count -eq 6 ]]; then # 6 attempts == 3 minutes at 30 seconds per host check attempt
        echo "couldn't connect. Attempting to retrieve new config..."
        wg-quick down ./wg-configs/custom_config.conf
        rm -f wg-configs/custom_config.conf
        create_new_config
        count=0
    fi
done
echo "Connection established!"

# Periodic connectivity check & auto-recovery
(
while true; do
    if ! ./check_host.sh 10.123.123.1; then
        echo "Lost connection to 10.123.123.1. Attempting recovery..."
        wg-quick down ./wg-configs/custom_config.conf
        sleep 2
        wg-quick up ./wg-configs/custom_config.conf

        # Wait and recheck connection
        retry=0
        while ! ./check_host.sh 10.123.123.1; do
            ((retry++))
            echo "Reconnection attempt $retry failed..."
            sleep 2
            if [[ $retry -eq 2 ]]; then
                echo "Failed to reconnect. Regenerating config..."
                wg-quick down ./wg-configs/custom_config.conf
                rm -f wg-configs/custom_config.conf
                create_new_config
                break
            fi
        done

        echo "Connection re-established!"
    fi
    sleep 30 # Check every 30 seconds
done
) &

# Conditionally start server processes
if [[ -n "$ENABLED_FOR_REQUESTS" ]]; then
    IFS=',' read -ra enabled_endpoints <<< "$ENABLED_FOR_REQUESTS"

    # Read total GPU memory available into an integer
    # Function to get total GPU memory in MiB for a specific GPU ID (default 0)
    get_total_gpu_memory_mib() {
        local gpu_id="${1:-0}"
        nvidia-smi --query-gpu=memory.total --format=csv,noheader --id="$gpu_id" | awk -F' ' '{print $1}'
    }

    # Function to start vllm server
    start_vllm_server() {
        local model="$1"
        local task="$2"
        local port="$3"
        local memory_fraction="$4"
        local extra_args="$5"
        echo "Starting vllm serve for $task on port $port (GPU $gpu_id) with memory fraction $memory_fraction"
        PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True vllm serve "$model" --task "$task" --host 0.0.0.0 --port "$port" --gpu-memory-utilization "$memory_fraction" $extra_args &
    }

    # Function to start gunicorn server
    start_gunicorn_server() {
        local port="$1"
        echo "Starting gunicorn serve for chunking on port $port"
        cd /app/chunker_server
        /root/miniconda3/bin/conda run -n py312 gunicorn --log-level debug --enable-stdio-inheritance --bind 0.0.0.0:$port app:app --workers "$CHUNKER_WORKERS" --access-logfile /proc/1/fd/1 --error-logfile /proc/1/fd/2 &
    }

    total_gpu_memory_mib=$(get_total_gpu_memory_mib)
    echo "Total GPU memory detected: $total_gpu_memory_mib"
    remaining_memory_mib="$total_gpu_memory_mib"

    started_completions=false
    started_embeddings=false

    # Prioritize /v1/chat/completions
    for endpoint_config in "${enabled_endpoints[@]}"; do
        if [[ "$endpoint_config" == *"/v1/chat/completions"* ]]; then
            port=$(echo "$endpoint_config" | cut -d':' -f2 | cut -d'/' -f1)
            echo "Attempting to start vllm serve for completions on port $port"
            completion_memory_needed_mib=16000
            if [[ "$remaining_memory_mib" -gt "$completion_memory_needed_mib" ]]; then
                completion_memory_fraction_float=$(awk "BEGIN{printf \"%.4f\", $completion_memory_needed_mib / $total_gpu_memory_mib}")
                echo "Starting vllm serve for completions on port $port with fraction $completion_memory_fraction_float"
                #mkdir -p /app/models
                #cd /app/models
                #test -f DeepSeek-R1-Distill-Qwen-14B-Q2_K_L.gguf || wget https://huggingface.co/unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-14B-Q2_K_L.gguf
                # unsloth/DeepSeek-R1-Distill-Qwen-14B-bnb-4bit --enforce-eager --enable-reasoning --reasoning-parser deepseek_r1 --quantization bitsandbytes --load-format bitsandbytes --enable-chunked-prefill --max-num-seqs 1 /// this worked @ 16384, and according to https://huggingface.co/spaces/NyxKrage/LLM-Model-VRAM-Calculator using GGUF and Q4_K_M it should support 25600 context size with 15.01GB used total
                start_vllm_server "unsloth/DeepSeek-R1-Distill-Qwen-14B-bnb-4bit" generate "$port" "$completion_memory_fraction_float" "--tokenizer unsloth/DeepSeek-R1-Distill-Qwen-14B --load-format bitsandbytes --quantization bitsandbytes --max_model_len 25600 --max-model-len 25600 --max-num-seqs 1 --enforce-eager --enable-reasoning --reasoning-parser deepseek_r1" # --enable-chunked-prefill oddly necessary to avoid OOM issues with this particular model serve setup
                remaining_memory_mib=$((remaining_memory_mib - completion_memory_needed_mib))
                started_completions=true
            else
                echo "Not enough GPU memory to start completions server (needed: ${completion_memory_needed_mib}MiB, available: ${remaining_memory_mib}MiB)"
            fi
            break # Prioritize and start only one completions server
        fi
    done

    # Then do /v1/embeddings if enough remains
    for endpoint_config in "${enabled_endpoints[@]}"; do
        if [[ "$endpoint_config" == *"/v1/embeddings"* ]]; then
            port=$(echo "$endpoint_config" | cut -d':' -f2 | cut -d'/' -f1)
            echo "Attempting to start vllm serve for embeddings on port $port"
            embedding_memory_needed_mib=4000
            if [[ "$remaining_memory_mib" -gt "$embedding_memory_needed_mib" ]]; then
                embedding_memory_fraction_float=$(awk "BEGIN{printf \"%.4f\", $embedding_memory_needed_mib / $total_gpu_memory_mib}")
                echo "Starting vllm serve for embeddings on port $port with fraction $embedding_memory_fraction_float"
                start_vllm_server "infly/inf-retriever-v1-1.5b" embed "$port" "$embedding_memory_fraction_float"
                remaining_memory_mib=$((remaining_memory_mib - embedding_memory_needed_mib))
                started_embeddings=true
            else
                echo "Not enough remaining GPU memory to start embeddings server (needed: ${embedding_memory_needed_mib}MiB, available: ${remaining_memory_mib}MiB)"
            fi
            break # Start only one embeddings server
        fi
    done

    # Start chunker servers regardless of vllm status
    for endpoint_config in "${enabled_endpoints[@]}"; do
        if [[ "$endpoint_config" == *"/api/chunk"* ]]; then
            port=$(echo "$endpoint_config" | cut -d':' -f2 | cut -d'/' -f1)
            start_gunicorn_server "$port"
        fi
    done
fi

while true; do
  sleep 10
done