# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

from dataclasses import dataclass
import logging
from grpc_wrapper.grpc_client import GRPCClient

logger = logging.getLogger(__name__)


@dataclass
class DeviceUsage:
    """Context manager for device operations that ensures proper cleanup."""

    grpc_client: GRPCClient
    device_id: str

    def __enter__(self) -> None:
        """Enter the context manager."""
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context manager and perform cleanup.

        Ensures that any installed apps are uninstalled, logcat collection is stopped,
        and the device is unlocked, even if an error occurs.
        """
        cleanup_operations = [
            (self.grpc_client.uninstall_app, "Uninstalling app"),
            (self.grpc_client.stop_logcat_collect, "Stopping logcat collection"),
            (self.grpc_client.unlock_device, "Unlocking device"),
        ]

        for operation, description in cleanup_operations:
            try:
                operation(self.device_id)
            except Exception as e:
                logger.warning(
                    f"{description} failed for device {self.device_id}: {str(e)}"
                )
                continue
