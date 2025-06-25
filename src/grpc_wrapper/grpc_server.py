# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import argparse
import logging
import os
import time
import grpc
import json
import platform
import threading
import tempfile
from concurrent import futures
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from utils.clogger import create_file_logger
from dotenv import load_dotenv

if not load_dotenv():
    print("Could not load .env!")
    import sys

    sys.exit(-1)

REL_DIR: Path = Path(os.environ["REL_DIR"])
logger = create_file_logger(
    "log_rpc_server.log", REL_DIR / "Logs", "GRPC_SRV", logging.DEBUG
)

# NOTE: avoid spamming the log on macos, https://github.com/grpc/grpc/issues/37642
os.environ["GRPC_VERBOSITY"] = "NONE"

import grpc_files.communication_pb2 as communication_pb2  # noqa: E402
import grpc_files.communication_pb2_grpc as communication_pb2_grpc  # noqa: E402

# Expects .env file to be loaded
from utils.apktools import sign_apk, aab_to_apk  # noqa: E402
from utils.device import Device, get_all_devices  # noqa: E402


CHUNK_SIZE = 1024 * 1024  # 1MB chunk size


class VTestStation:
    devices = {}

    # one lock per grcp_server, adb cannot be executed in parallel, most likely
    # due to port usage
    adb_lock = threading.Lock()

    @staticmethod
    def add_device(device: Device):
        if device.id in VTestStation.devices:
            # TODO: think about this, if a device changes from offline to online
            return

        VTestStation.devices[device.id] = device

    @staticmethod
    def del_device(device_id: str):
        if device_id in VTestStation.devices:
            del VTestStation.devices[device_id]

    @staticmethod
    def get_device(id: str) -> Device | None:
        if id in VTestStation.devices:
            return VTestStation.devices[id]
        return None

    @staticmethod
    def devices_as_dict() -> list:
        return [
            {"id": d.id, "in_use": d.in_use} | d.properties
            for d in VTestStation.devices.values()
        ]


class RPCResponse(BaseModel):
    """Base model for all RPC responses."""

    result: Any = None
    error: str = ""


class DeviceListResponse(RPCResponse):
    """Response for device list operations."""

    result: list[dict[str, Any]] = Field(default_factory=list)


class DeviceIdResponse(RPCResponse):
    """Response for device ID operations."""

    result: list[str] = Field(default_factory=list)


class BooleanResponse(RPCResponse):
    """Response for boolean operations."""

    result: bool = False


class LogcatResponse(RPCResponse):
    """Response for logcat operations."""

    result: str | int | None = None


class VTestFunctions:
    @staticmethod
    def getAdbDevices() -> DeviceListResponse:
        logger.info("getAdbDevices called")
        try:
            devices = get_all_devices()
        except Exception as e:
            return DeviceListResponse(error=f"Get all devices failed: {str(e)}")

        for id, properties in devices.items():
            if properties["state"] != "device":
                # to make things easy, do not append non online device
                continue
            VTestStation.add_device(
                Device(id=id, properties=properties, adb_lock=VTestStation.adb_lock)
            )

        # account for removed devices, unplugged or emulator got closed
        for devid in list(VTestStation.devices.keys()).copy():
            if devid not in list(devices.keys()):
                VTestStation.del_device(devid)

        return DeviceListResponse(result=VTestStation.devices_as_dict())

    @staticmethod
    def getFreeDevices(num_devices: int, wish_devices: list) -> DeviceIdResponse:
        logger.info(f"getFreeDevices called, num: {num_devices}, wish: {wish_devices}")

        ret_devices = []

        def add_device(device: Device):
            device.in_use = True
            ret_devices.append(device.id)

        if len(VTestStation.devices) == 0:
            VTestFunctions.getAdbDevices()

        for device in VTestStation.devices.values():
            if device.in_use:
                continue

            if len(wish_devices) == 0:
                if len(ret_devices) == num_devices:
                    break
                add_device(device)
            else:
                if device.id in wish_devices:
                    add_device(device)

        if len(ret_devices) == 0:
            return DeviceIdResponse(error="No free devices")
        return DeviceIdResponse(result=ret_devices)

    @staticmethod
    def installApp(did: str, server_file: Path, sign_file: bool) -> BooleanResponse:
        logger.info(f"installApp called: {did} {server_file}")

        app_file = REL_DIR / server_file
        if not app_file.exists():
            return BooleanResponse(error="Appfile not found!")

        if app_file.suffix != ".apk":
            return BooleanResponse(error="only .apk files can be installed!")

        try:
            signed_file = app_file
            if sign_file:
                if (
                    signed_file := sign_apk(app_file, with_aligning=True, logger=logger)
                ) is None:
                    logger.error("Could not sign apk")
                    return BooleanResponse(error="Could not sign apk")

            dev = VTestStation.get_device(did)
            if dev is None:
                logger.error(f"Device not found: {dev}")
                return BooleanResponse(error=f"Device not found: {dev}")

            error, stderr_msg = dev.install_apk(signed_file)
            if error:
                return BooleanResponse(error=stderr_msg)

            logger.info("Installed APK successful")
            return BooleanResponse(result=True)
        except Exception as e:
            return BooleanResponse(error=f"Installation Exception: {str(e)}")

    @staticmethod
    def uninstallApp(did: str) -> BooleanResponse:
        logger.info(f"uninstallApp called: {did}")
        if (dev := VTestStation.get_device(did)) is None:
            return BooleanResponse(
                error=f"Uninstall app failed, device not available: {did}"
            )

        try:
            if dev.uninstall_apk():
                return BooleanResponse(result=True)
            return BooleanResponse(error="Uninstall Failed!")
        except Exception as e:
            return BooleanResponse(error=f"uninstallApp failed: {str(e)}")

    @staticmethod
    def isPackageNameInstalled(did: str, package_name: str) -> BooleanResponse:
        logger.info(f"isPackageNameInstalled: {package_name}")
        if (dev := VTestStation.get_device(did)) is None:
            return BooleanResponse(
                error=f"isPackageNameInstalled failed, device not available: {did}"
            )
        return BooleanResponse(result=dev.is_package_installed(package_name))

    @staticmethod
    def startLogcatCollect(did: str) -> LogcatResponse:
        logger.info(f"startLogcatCollect called: {did}")
        if (dev := VTestStation.get_device(did)) is None:
            return LogcatResponse(
                error=f"Logcat start failed, device not available: {did}"
            )

        try:
            dev.clear_logcat()
            logcat_pid = dev.start_collect_logcat()
            time.sleep(1)
            return LogcatResponse(result=logcat_pid)
        except Exception as e:
            return LogcatResponse(error=f"Logcat start failed: {str(e)}")

    @staticmethod
    def stopLogcatCollect(did: str) -> LogcatResponse:
        logger.info(f"stopLogcatCollect called: {did}")
        if (dev := VTestStation.get_device(did)) is None:
            return LogcatResponse(
                error=f"Logcat stop failed, device not available: {did}"
            )

        try:
            logcat_file = dev.stop_collect_logcat()
            if logcat_file is None:
                return LogcatResponse(error="Logcat stop failed, no output file!")
            return LogcatResponse(result=logcat_file)
        except Exception as e:
            return LogcatResponse(error=f"Logcat stop failed: {str(e)}")

    @staticmethod
    def runLastInstalledApk(
        did: str, execution_time: int, custom_cmd: str
    ) -> BooleanResponse:
        """
        1. start app
        2. waits `execution_time`[s]
        3. kill app
        """
        logger.info(f"runLastInstalledApk called: {did}, '{custom_cmd}'")

        if (dev := VTestStation.get_device(did)) is None:
            return BooleanResponse(
                error=f"runLastInstalledApk failed, device not available: {did}"
            )

        if not dev.run_apk(custom_cmd=custom_cmd):
            return BooleanResponse(error="No App specified")

        time.sleep(2)  # 2s for startup

        ret = dev.is_running()
        if ret is None:
            return BooleanResponse(error="App not running, startup fail!")

        dev.running_app_pid = ret
        logger.info(f"Running app PID {did} {ret}, execution time: {execution_time}")

        time.sleep(execution_time)  # wait execution time

        if not dev.kill_apk():
            return BooleanResponse(error="Killing failed after execution")
        return BooleanResponse(result=True)

    @staticmethod
    def killApp(did: str) -> BooleanResponse:
        logger.info(f"killApp called: {did}")

        if (dev := VTestStation.get_device(did)) is None:
            return BooleanResponse(error=f"killApp failed, device not available: {did}")

        if dev.kill_apk():
            return BooleanResponse(result=True)
        else:
            return BooleanResponse(error="Killing app failed")

    @staticmethod
    def unlockDevice(did: str) -> BooleanResponse:
        logger.info(f"unlockDevice called: {did}")
        if did == "__ALL__":
            for dev in VTestStation.devices.values():
                dev.in_use = False
            return BooleanResponse(result=True)

        if (dev := VTestStation.get_device(did)) is None:
            return BooleanResponse(error=f"Device not found: {did}")
        dev.in_use = False
        return BooleanResponse(result=True)


class CommunicationServicer(communication_pb2_grpc.CommunicationService):
    def UploadFile(self, request_iterator, context):
        first_request = next(request_iterator)

        logger.info(
            f"UploadFile: {first_request.filename} {first_request.storage_path}"
        )

        filename = first_request.filename

        # storage_path is relative to REL_DIR
        storage_path = REL_DIR / first_request.storage_path
        storage_path.mkdir(exist_ok=True)

        # Open the file in binary write mode
        with open(storage_path / filename, "wb") as f:
            # Write the first chunk
            f.write(first_request.chunk_data)

            # Write subsequent chunks
            for request in request_iterator:
                f.write(request.chunk_data)

        return communication_pb2.FileUploadResponse(message="OK")  # type: ignore

    def UploadApp(self, request_iterator, context):
        """
        All uploaded apps are automatically stored in `REL_DIR/UploadedFiles`

        If the same file (checked by name) already exists on the server an
        error will be returned.

        TODO: might wanna rename the function to upload android app
        """

        first_request = next(request_iterator)
        logger.info(f"UploadApp: {first_request.filename}")

        # storage_path is relative to REL_DIR
        storage_path = REL_DIR / "UploadedFiles"
        storage_path.mkdir(exist_ok=True)

        # create tempfile to handle uniquiness
        tmp_file = tempfile.NamedTemporaryFile(
            dir=storage_path, delete=False, suffix=Path(first_request.filename).suffix
        )
        store_file = Path(tmp_file.name)
        logger.info(f"Store File: {store_file}")

        if store_file.suffix not in [".apk", ".aab"]:
            return communication_pb2.AppUploadResponse(  # type: ignore
                error="Only .aab and .apk Supported!", stored_filename=""
            )

        # Open the file in binary write mode
        with open(store_file, "wb") as f:
            # Write the first chunk
            f.write(first_request.chunk_data)

            # Write subsequent chunks
            for request in request_iterator:
                f.write(request.chunk_data)

        if store_file.suffix == ".apk":
            return communication_pb2.AppUploadResponse(  # type: ignore
                error="", stored_filename=f"UploadedFiles/{store_file.name}"
            )

        # if the uploaded file is an aab convert it once to apk
        logger.info("Convert aab to apk")

        # overwrite app_file
        # NOTE: this is out of place here, goal is to only convert aab files
        # once to apks per server, therefore it was done here
        if (store_file := aab_to_apk(store_file)) is None:
            return communication_pb2.AppUploadResponse(  # type: ignore
                error=".aab file could be converted to .apk!", stored_filename=""
            )
        return communication_pb2.AppUploadResponse(  # type: ignore
            error="",
            stored_filename=f"UploadedFiles/{store_file.parent.name}/{store_file.name}",
        )

    def PullFile(self, request, context):
        if not os.path.exists(request.filename):
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("File not found")
            return communication_pb2.PullFileRequest(error="File not found")  # type: ignore

        with open(request.filename, "rb") as f:
            while True:
                chunk_data = f.read(CHUNK_SIZE)
                if not chunk_data:
                    break
                yield communication_pb2.PullFileResponse(  # type: ignore
                    error="", chunk_data=chunk_data
                )

    def GetOperatingSystem(self, req, context):
        return communication_pb2.OperatingSystemResponse(os=platform.system())  # type: ignore

    def GetAdbDevices(self, req, context):
        ret = VTestFunctions.getAdbDevices()

        # encode devices as json string, to easily be able to add new properties
        return communication_pb2.GetAdbDevicesResponse(  # type: ignore
            error=ret.error, devices=json.dumps(ret.result)
        )

    def GetFreeDevices(self, req, context):
        ret = VTestFunctions.getFreeDevices(req.num_devices, req.device_list)
        return communication_pb2.GetFreeDevicesResponse(  # type: ignore
            error=ret.error, device_list=ret.result
        )

    def UnlockDevice(self, req, context):
        ret = VTestFunctions.unlockDevice(req.device_id)
        return communication_pb2.UnlockDeviceResponse(error=ret.error)  # type: ignore

    def InstallApp(self, req, context):
        ret = VTestFunctions.installApp(
            req.device_id, Path(req.server_path), req.sign_app
        )
        return communication_pb2.InstallAppResponse(error=ret.error)  # type: ignore

    def UninstallApp(self, req, context):
        ret = VTestFunctions.uninstallApp(req.device_id)
        return communication_pb2.UninstallAppResponse(error=ret.error)  # type: ignore

    def IsPackageNameInstalled(self, req, context):
        ret = VTestFunctions.isPackageNameInstalled(req.device_id, req.package_name)
        return communication_pb2.IsPackageNameInstalledResponse(installed=ret.result)  # type: ignore

    def StartLogcatCollect(self, req, context):
        ret = VTestFunctions.startLogcatCollect(req.device_id)
        return communication_pb2.StartLogcatCollectResponse(  # type: ignore
            error=ret.error, logcat_pid=ret.result
        )

    def StopLogcatCollect(self, req, context):
        ret = VTestFunctions.stopLogcatCollect(req.device_id)
        return communication_pb2.StopLogcatCollectResponse(  # type: ignore
            error=ret.error, logcat_file=ret.result
        )

    def RunLastInstalledApk(self, req, context):
        ret = VTestFunctions.runLastInstalledApk(
            req.device_id, req.execution_time, req.custom_cmd
        )
        return communication_pb2.RunLastInstalledApkResponse(  # type: ignore
            error="" if ret.error is None else ret.error
        )

    def KillApp(self, req, context):
        ret = VTestFunctions.killApp(req.device_id)
        return communication_pb2.KillAppResponse(  # type: ignore
            error="" if ret.error is None else ret.error
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p",
        "--port",
        default="8080",
    )
    args = parser.parse_args()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    communication_pb2_grpc.add_CommunicationServiceServicer_to_server(
        CommunicationServicer(), server
    )
    server.add_insecure_port(f"[::]:{args.port}")

    server.start()
    logger.info(f"Server started, listening on port {args.port}...")

    try:
        while True:
            time.sleep(86400 * 2)  # Keep the server running for 2 day's
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    main()
