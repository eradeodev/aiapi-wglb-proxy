#!/bin/bash

directory="/app/peers"
output_file="hub-wg.conf"

# Ensure directory exists
mkdir -p "$directory"

# Concatenate all files in the directory into the output file
rm -f "$output_file"
cp "base_config.txt" "$output_file"
if [ -d "$directory" ]; then
    for file in ; do
        filename=$(basename "$file")
        cat "$filename" >> "$output_file"
    done
fi
