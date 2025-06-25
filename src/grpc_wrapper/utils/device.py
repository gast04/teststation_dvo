# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import logging
import os
import subprocess
import time
import tempfile
import threading
from enum import Enum
from pathlib import Path
from typeguard import typechecked
from dataclasses import dataclass
from typing import Any

from utils.clogger import create_file_logger
from utils.apktools import get_package_name

# Constants
ADB_BIN: str = "adb"  # adb is in each docker container
ADB_EXECUTION_TIMEOUT: int = 10
REL_DIR: Path = Path(os.environ["REL_DIR"])
VALID_DEVICE_STATES: set[str] = {"device", "unauthorized"}
DEVICE_PROPERTIES: dict[str, str] = {
    "ro.product.cpu.abi": "",
    "ro.product.model": "",
    "ro.build.version.release": "",
    "ro.build.version.sdk": "",
    "ro.product.manufacturer": "",
}

logger = create_file_logger("log_device.log", REL_DIR / "Logs", "DEVICE", logging.DEBUG)


def linfo(devid: str, msg: str) -> None:
    """Log info message with device ID prefix."""
    logger.info(f"{devid} | {msg}")


def lerror(devid: str, msg: str) -> None:
    """Log error message with device ID prefix."""
    logger.error(f"{devid} | {msg}")


def get_all_devices() -> dict[str, dict[str, Any]]:
    """Get all connected devices and their properties.

    Returns:
        Dictionary mapping device IDs to their properties.
    """
    try:
        out = subprocess.check_output([ADB_BIN, "devices"], timeout=3)
    except subprocess.TimeoutExpired:
        return {}

    lines = out.decode("utf-8", "ignore").split("\n")
    devices: dict[str, dict[str, Any]] = {}

    for line in lines:
        tmp = line.strip().split("\t")
        if len(tmp) != 2 or tmp[1] not in VALID_DEVICE_STATES:
            continue
        devices[tmp[0]] = {"state": tmp[1]}

    # Fetch device properties
    for devid, vals in devices.items():
        properties = DEVICE_PROPERTIES.copy()
        try:
            p = subprocess.Popen(
                [ADB_BIN, "-s", devid, "shell", "getprop"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert p.stdout is not None
            props = p.stdout.readlines()

            for prop in props:
                prop = prop.decode("utf-8", errors="replace").strip()
                for key in properties:
                    if f"[{key}]" in prop:
                        properties[key] = prop.split(":")[1].strip()[1:-1]
                        break

            vals.update(properties)
        except Exception as e:
            lerror(devid, f"Failed to get device properties: {str(e)}")
            continue

    return devices


class DeviceState(Enum):
    """Device connection states."""

    OFF = 0
    ONLINE = 1
    UNAUTHORIZED = 2


@dataclass
class Device:
    """Represents an Android device with ADB connection."""

    id: str
    properties: dict[str, Any]
    adb_lock: threading.Lock
    in_use: bool = False
    last_installed_app: str | None = None
    running_app_pid: int = 0
    logcat_file: str = ""
    logcat_collect_process: subprocess.Popen | None = None
    logout_tempfile = None

    def __post_init__(self) -> None:
        """Validate initialization parameters."""
        if not isinstance(self.properties, dict):
            raise TypeError("properties must be a dictionary")

    def __del__(self) -> None:
        """Cleanup resources on deletion."""
        if self.logcat_collect_process is None:
            return

        try:
            self.logcat_collect_process.kill()
        except Exception as e:
            lerror(self.id, f"Failed to kill logcat process: {str(e)}")
        finally:
            self.logcat_collect_process = None

    def __str__(self) -> str:
        """String representation of the device."""
        return f"Device {self.id} - {'in use' if self.in_use else 'available'}"

    def __dict__(self) -> dict[str, Any]:
        """Dictionary representation of the device."""
        return {"id": self.id, "in_use": self.in_use}

    def unlock(self) -> None:
        """Mark device as available for use."""
        self.in_use = False

    @typechecked
    def _exec_adb(self, cmd: list[str]) -> tuple[str, str | None]:
        """Execute ADB command and return stdout and stderr.

        Args:
            cmd: List of command arguments to execute

        Returns:
            Tuple of (stdout, stderr). stderr is None if no error occurred.
        """
        with self.adb_lock:
            try:
                e = [ADB_BIN, "-s", self.id] + cmd
                linfo(self.id, f"ADB CMD: {' '.join(e)}")
                p = subprocess.Popen(e, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = p.communicate(timeout=ADB_EXECUTION_TIMEOUT)

                # Safely decode output, replacing invalid characters
                stdout_str = stdout.decode("utf-8", errors="replace")
                if stderr:
                    stderr_str = stderr.decode("utf-8", errors="replace")
                    linfo(self.id, f"ExecADB ERR: {stderr_str}")
                    return stdout_str, stderr_str
                return stdout_str, None

            except subprocess.TimeoutExpired as e:
                lerror(self.id, f"Error adb command timed out: {str(e)}")
                return "", str(e)
            except Exception as e:
                lerror(self.id, f"Error executing adb command: {str(e)}")
                return "", str(e)

    @typechecked
    def _logcat(self, msg: str) -> None:
        """Send message to logcat for easier parsing."""
        cmd = ["shell", "log", "-t", "DENUVO_DEBUG", msg]
        self._exec_adb(cmd)
        time.sleep(0.2)

    @typechecked
    def install_apk(self, filename: Path) -> tuple[bool, str]:
        """Install APK on device.

        Args:
            filename: Path to APK file

        Returns:
            Tuple of (error_occurred, error_message)
        """
        if not filename.exists():
            return True, "File to install not found!"

        # it can be that an app with the same package name is already installed
        # this needs to be uninstalled first.

        if (pname := get_package_name(filename, logger=logger)) is None:
            logger.error(f"Could not get package name: {filename}")
            return True, "Could not get package name"

        self.uninstall_apk(package_name=pname)
        self._logcat(f"Installing APK: {filename.as_posix()}")

        _, stderr = self._exec_adb(["install", filename.as_posix()])

        # NOTE: install often writes to stderr even on successful installs,
        # like: `All files should be loaded. Notifying the device.`
        # therefore the package name is checked if it is really there.
        if self.is_package_installed(pname):
            # on successful install set last installed package name
            logger.info(f"Install package name: {pname}")
            self.last_installed_app = pname
            return False, ""

        logger.error("App not correctly installed!")
        msg = "Could not install App, stderr: "
        if stderr is not None:
            msg += stderr
        return True, msg

    @typechecked
    def is_package_installed(self, package_name: str) -> bool:
        """Check if package is installed on device."""
        cmd = ["shell", "pm", "list", "packages"]
        stdout, _ = self._exec_adb(cmd)
        return any(package_name.lower() in line.lower() for line in stdout.split("\n"))

    @typechecked
    def uninstall_apk(self, package_name: str | None = None) -> bool:
        """Uninstall APK from device."""
        if package_name is not None:
            del_package = package_name
        elif self.last_installed_app is not None:
            del_package = self.last_installed_app
            self.last_installed_app = None
        else:
            logger.error(
                "uninstall called without last_installed_app or package_name specified!"
            )
            return False

        self._logcat(f"Uninstall: {del_package}")
        self._exec_adb(["uninstall", del_package])
        return True

    @typechecked
    def run_apk(self, custom_cmd: str = "") -> bool:
        """Run installed APK on device."""
        if self.last_installed_app is None:
            lerror(self.id, "No APK installed!")
            return False

        if custom_cmd:
            linfo(self.id, f"Custom run app command: {custom_cmd}")
            cmd = custom_cmd.split(" ")
        else:
            cmd = [
                "shell",
                "monkey",
                "-p",
                self.last_installed_app,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ]

        self._logcat(f"Starting APK: {self.last_installed_app}, Device: {self.id}")
        self._exec_adb(cmd)
        return True

    def kill_apk(self) -> bool:
        """Force stop and clear app data."""
        if not self.last_installed_app:
            return False

        cmd_force = ["shell", "am", "force-stop", self.last_installed_app]
        cmd_clear = ["shell", "pm", "clear", self.last_installed_app]

        try:
            self._logcat(f"Stopping APP: {self.last_installed_app}")
            out, err = self._exec_adb(cmd_clear)

            if out.strip() != "Success" or err is not None:
                self._logcat(f"Stopped APP (ERROR): {self.last_installed_app}")
                lerror(self.id, f"stop application error: {err}")
                self._exec_adb(cmd_force)

            self._logcat(f"Stopped APP: {self.last_installed_app}")
            return True

        except Exception as e:
            lerror(self.id, f"Error within kill_apk: {str(e)}")
            return False

    @typechecked
    def is_running(self, pname: str = "") -> int | None:
        """Check if app is running and return its PID."""
        if not pname:
            if self.last_installed_app is None:
                return None
            pname = self.last_installed_app

        cmd = ["shell", f"pidof {pname}"]
        try:
            stdout, stderr = self._exec_adb(cmd)
            if stderr is not None:
                lerror(self.id, f"running pidof error: {stderr}")

            logger.debug(f"is_running: {stdout} (raw)")
            pids = stdout.split()

            if pids and pids[0].isdigit():
                return int(pids[0])
            return None

        except Exception as e:
            lerror(self.id, f"Error within is_running: {str(e)}")
            return None

    def clear_logcat(self) -> None:
        """Clear device logcat buffer."""
        self._exec_adb(["logcat", "-c"])

    def start_collect_logcat(self) -> int:
        """Start collecting logcat output to file.

        The output is written directly to a file in binary mode to handle any character encoding.
        """
        cmd = [ADB_BIN, "-s", self.id, "logcat"]

        # directly write to a file
        self.logout_tempfile = tempfile.NamedTemporaryFile(delete=False)
        linfo(self.id, f"Start logcat collection, outfile: {self.logout_tempfile.name}")

        try:
            self.logcat_collect_process = subprocess.Popen(
                cmd, stdout=self.logout_tempfile.file, stderr=subprocess.PIPE
            )
            return self.logcat_collect_process.pid
        except Exception as e:
            lerror(self.id, f"Failed to start logcat collection: {str(e)}")
            if self.logout_tempfile:
                self.logout_tempfile.close()
                self.logout_tempfile = None
            raise

    def stop_collect_logcat(self) -> str | None:
        """Stop collecting logcat and return log file path."""
        self._logcat(f"Stop logcat - app: {self.last_installed_app}")

        if self.logcat_collect_process is None:
            lerror(self.id, "Stop logcat: process is None")
            return None

        if self.logout_tempfile is None:
            lerror(self.id, "Stop logcat: tempfile is None")
            return None

        if self.logcat_collect_process.poll() is not None:
            linfo(self.id, "logcat process NOT running")

        try:
            self.logcat_collect_process.terminate()
            self.logcat_collect_process.wait(timeout=5)  # Wait for process to terminate
        except subprocess.TimeoutExpired:
            lerror(self.id, "logcat process did not terminate gracefully")
            try:
                self.logcat_collect_process.kill()
            except Exception as e:
                lerror(self.id, f"Failed to kill logcat process: {str(e)}")
        except Exception as e:
            lerror(self.id, f"Stop logcat: terminate error: {str(e)}")
            return None

        logcat_file = self.logout_tempfile.name
        self.logcat_collect_process = None
        self.logout_tempfile = None
        return logcat_file
