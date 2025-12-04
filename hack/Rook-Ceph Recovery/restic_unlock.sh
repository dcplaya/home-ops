#!/bin/bash

# Prompt for AWS_ACCESS_KEY_ID if not set
if [ -z "$AWS_ACCESS_KEY_ID" ]; then
    read -rp "Enter AWS_ACCESS_KEY_ID: " AWS_ACCESS_KEY_ID
    export AWS_ACCESS_KEY_ID
fi

# Prompt for AWS_SECRET_ACCESS_KEY if not set
if [ -z "$AWS_SECRET_ACCESS_KEY" ]; then
    read -rsp "Enter AWS_SECRET_ACCESS_KEY: " AWS_SECRET_ACCESS_KEY
    echo
    export AWS_SECRET_ACCESS_KEY
fi

# Prompt for RESTIC_PASSWORD if not set
if [ -z "$RESTIC_PASSWORD" ]; then
    read -rsp "Enter RESTIC_PASSWORD: " RESTIC_PASSWORD
    echo
    export RESTIC_PASSWORD
fi

# Prompt for RESTIC_REPOSITORY if not supplied as an argument
if [ -z "$1" ]; then
    read -rp "Enter Restic S3 repository path (e.g., s3:s3.amazonaws.com/my-bucket/path): " RESTIC_REPOSITORY
else
    RESTIC_REPOSITORY="$1"
fi
export RESTIC_REPOSITORY

# Run restic unlock
echo "Unlocking restic repository at '$RESTIC_REPOSITORY'..."
restic unlock

# Capture exit status
if [ $? -eq 0 ]; then
    echo "Repository unlocked successfully."
else
    echo "Failed to unlock repository."
fi
