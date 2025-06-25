# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import os
import logging
from pathlib import Path

from paramiko import AutoAddPolicy, SFTPClient, SSHClient
from typeguard import typechecked

logger = logging.getLogger(__name__)


@typechecked
def create_sshclient() -> SSHClient | None:
    """Create and configure an SSH client.

    Creates an SSH client using environment variables for connection details.
    The client is configured to automatically accept unknown host keys.

    Returns:
        SSHClient | None: Configured SSH client if successful, None if environment
            variables are missing or connection fails.

    Raises:
        Exception: Any exception that occurs during SSH client creation or connection
            is logged and None is returned.
    """
    if not all(e in os.environ for e in ["REMOTE_HOST", "REMOTE_USER", "REMOTE_PWD"]):
        logger.error(
            "Not all required Environment Variables set! [REMOTE_HOST, REMOTE_USER, REMOTE_PWD]"
        )
        return None

    try:
        ssh_client = SSHClient()
        ssh_client.set_missing_host_key_policy(AutoAddPolicy())
        ssh_client.connect(
            hostname=os.environ["REMOTE_HOST"],
            username=os.environ["REMOTE_USER"],
            password=os.environ["REMOTE_PWD"],
        )
        return ssh_client
    except Exception as e:
        logger.error(f"Failed to create SSH client: {str(e)}", exc_info=True)
        return None


@typechecked
def create_ftpclient() -> SFTPClient | None:
    """Create an SFTP client using an SSH connection.

    Creates an SFTP client using an existing SSH connection. The SSH connection
    is created if it doesn't exist.

    Returns:
        SFTPClient | None: Configured SFTP client if successful, None if SSH
            connection fails or SFTP client creation fails.

    Raises:
        Exception: Any exception that occurs during SFTP client creation is logged
            and None is returned.
    """
    if (ssh_client := create_sshclient()) is None:
        return None

    try:
        return ssh_client.open_sftp()
    except Exception as e:
        logger.error(f"Failed to create SFTP client: {str(e)}", exc_info=True)
        return None


@typechecked
def ftp_upload_file(filename: Path, storage_path: Path) -> bool:
    """Upload a file using SFTP.

    Args:
        filename: Path to the local file to upload.
        storage_path: Path where the file should be stored on the remote server.

    Returns:
        bool: True if upload was successful, False otherwise.

    Raises:
        AssertionError: If the local file doesn't exist.
        Exception: Any exception that occurs during upload is logged and False
            is returned.
    """
    assert filename.exists(), f"File does not exist: {filename.as_posix()}"

    if (ftp_client := create_ftpclient()) is None:
        return False

    try:
        ftp_client.put(filename.as_posix(), storage_path.as_posix())
    except Exception as e:
        logger.error(
            f"Failed to upload file {filename.as_posix()} to {storage_path.as_posix()}: {str(e)}",
            exc_info=True,
        )
        return False

    return True
