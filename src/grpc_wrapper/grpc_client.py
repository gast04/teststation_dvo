# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import grpc

from dataclasses import dataclass
from pathlib import Path
from typeguard import typechecked
from typing import Any

from grpc_wrapper.grpc_files import communication_pb2
from grpc_wrapper.grpc_files import communication_pb2_grpc

CHUNK_SIZE = 1024 * 1024  # 1MB chunk size


@dataclass
class GRPCResult:
    ret: Any | None = None
    err: str | None = None


@typechecked
def get_operating_system(stub) -> str:
    response = stub.GetOperatingSystem(communication_pb2.OperatingSystemRequest())  # type: ignore
    return response.os


@typechecked
def upload_file(stub, filename: Path, storage_path: Path) -> bool:
    def file_chunk_generator():
        with open(filename, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield communication_pb2.FileUploadRequest(  # type: ignore
                    filename=filename.name,
                    chunk_data=chunk,
                    storage_path=storage_path.as_posix(),
                )

    try:
        response = stub.UploadFile(file_chunk_generator())
        return response.message == "OK"

    except Exception:
        return False


@typechecked
def upload_app(stub, filename: Path) -> tuple[bool, str]:
    """
    All uploaded apps are stored in `REL_DIR/Teststation/TestFiles`
    Return:
        error <bool>
        if error:
            error_msg <str>
        else:
            stored_filname <str>

    The uploaded filename changes, to avoid having to users uploaded the same
    filename.
    """

    def file_chunk_generator():
        with open(filename, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield communication_pb2.AppUploadRequest(
                    filename=filename.name,
                    chunk_data=chunk,
                )

    try:
        response = stub.UploadApp(file_chunk_generator())
        if response.error != "":
            return True, response.stored_filename
        return False, response.stored_filename

    except Exception as e:
        return True, str(e)


@typechecked
def pull_file(stub, filename: Path) -> tuple[bool, str]:
    request = communication_pb2.PullFileRequest(filename=filename.as_posix())  # type: ignore
    full_response = bytes()
    try:
        response_stream = stub.PullFile(request)
        for chunk in response_stream:
            full_response += chunk.chunk_data
    except grpc.RpcError as e:
        return True, f"{e.code()}, {e.details()}"

    try:
        return False, full_response.decode("utf-8", "ignore")
    except Exception as e:
        return True, f"Decode Error: {str(e)}"


def get_adb_devices(stub):
    return stub.GetAdbDevices(communication_pb2.GetAdbDevicesRequest())  # type: ignore


@typechecked
def get_free_device(stub, num_devices: int, device_list: list):
    return stub.GetFreeDevices(
        communication_pb2.GetFreeDevicesRequest(  # type: ignore
            num_devices=num_devices, device_list=device_list
        )
    )


@typechecked
def unlock_device(stub, device_id: str):
    return stub.UnlockDevice(communication_pb2.UnlockDeviceRequest(device_id=device_id))  # type: ignore


@typechecked
def install_app(stub, device_id: str, server_path: Path, sign_app: bool):
    return stub.InstallApp(
        communication_pb2.InstallAppRequest(  # type: ignore
            device_id=device_id, server_path=server_path.as_posix(), sign_app=sign_app
        )
    )


@typechecked
def uninstall_app(stub, device_id: str):
    return stub.UninstallApp(communication_pb2.UninstallAppRequest(device_id=device_id))  # type: ignore


@typechecked
def is_package_name_installed(stub, device_id: str, package_name: str):
    return stub.IsPackageNameInstalled(
        communication_pb2.IsPackageNameInstalledRequest(  # type: ignore
            device_id=device_id, package_name=package_name
        )
    )


@typechecked
def start_logcat_collect(stub, device_id: str):
    return stub.StartLogcatCollect(
        communication_pb2.StartLogcatCollectRequest(  # type: ignore
            device_id=device_id,
        )
    )


@typechecked
def stop_logcat_collect(stub, device_id: str):
    return stub.StopLogcatCollect(
        communication_pb2.StopLogcatCollectRequest(  # type: ignore
            device_id=device_id,
        )
    )


@typechecked
def run_last_installed_apk(stub, device_id: str, execution_time: int, custom_cmd: str):
    return stub.RunLastInstalledApk(
        communication_pb2.RunLastInstalledApkRequest(  # type: ignore
            device_id=device_id,
            execution_time=execution_time,
            custom_cmd=custom_cmd,
        )
    )


@typechecked
def kill_app(stub, device_id: str):
    return stub.KillApp(
        communication_pb2.KillAppRequest(  # type: ignore
            device_id=device_id,
        )
    )


class GRPCClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

        try:
            self.get_operating_system()
        except Exception as e:
            raise Exception(f"Could not connect to the gRPC server: {str(e)}")

    def _execute_grpc_call(self, grpc_function, *args, **kwargs):
        with grpc.insecure_channel(f"{self.host}:{self.port}") as channel:
            stub = communication_pb2_grpc.CommunicationServiceStub(channel)
            ret = grpc_function(stub, *args, **kwargs)
        return ret

    @typechecked
    def upload_file(self, filename: Path, storage_path: Path) -> GRPCResult:
        ret = self._execute_grpc_call(upload_file, filename, storage_path)
        return GRPCResult(ret=True) if ret else GRPCResult(err="Upload failed")

    @typechecked
    def upload_app(self, filename: Path) -> GRPCResult:
        error, msg = self._execute_grpc_call(upload_app, filename)
        return GRPCResult(err=msg) if error else GRPCResult(ret=msg)

    @typechecked
    def pull_file(self, filename: Path) -> GRPCResult:
        error, result = self._execute_grpc_call(pull_file, filename)
        return GRPCResult(err=result) if error else GRPCResult(ret=result)

    @typechecked
    def get_operating_system(self) -> str:
        return self._execute_grpc_call(get_operating_system)

    @typechecked
    def get_adb_devices(self) -> GRPCResult:
        ret = self._execute_grpc_call(get_adb_devices)
        return GRPCResult(ret=ret.devices, err=None if ret.error == "" else ret.error)

    # @typechecked
    # list = None cannot be typechecked, empty list will run into modifiable default argument issue
    def get_free_device(
        self, num_devices: int = 1, device_list: list | None = None
    ) -> GRPCResult:
        """
        If a device list is provided, the function will return all devices from the list
        that are free. If the device list is empty, `num_devices` devices will be returned.

        Returned devices are locked for usage.

        NOTE: on first call after startup, if no devices are available
        `get_adb_devices` will be called to collect devices.
        """
        if device_list is None:
            device_list = []
        ret = self._execute_grpc_call(get_free_device, num_devices, device_list)
        return GRPCResult(
            ret=ret.device_list, err=None if ret.error == "" else ret.error
        )

    @typechecked
    def unlock_device(self, device_id: str) -> GRPCResult:
        # device_id can be "__ALL__" to unlock all devices
        # this is used for testing and should not be used in production

        ret = self._execute_grpc_call(unlock_device, device_id)
        return GRPCResult(
            ret=ret.error == "", err=None if ret.error == "" else ret.error
        )

    @typechecked
    def install_app(
        self, device_id: str, server_path: Path, sign_app: bool
    ) -> GRPCResult:
        ret = self._execute_grpc_call(install_app, device_id, server_path, sign_app)
        return GRPCResult(
            ret=ret.error == "", err=None if ret.error == "" else ret.error
        )

    @typechecked
    def uninstall_app(self, device_id: str) -> GRPCResult:
        ret = self._execute_grpc_call(uninstall_app, device_id)
        return GRPCResult(err=None if ret.error == "" else ret.error)

    @typechecked
    def is_package_name_installed(
        self, device_id: str, package_name: str
    ) -> GRPCResult:
        ret = self._execute_grpc_call(
            is_package_name_installed, device_id, package_name
        )
        return GRPCResult(ret=ret.installed)

    @typechecked
    def start_logcat_collect(self, device_id: str) -> GRPCResult:
        ret = self._execute_grpc_call(start_logcat_collect, device_id)
        return GRPCResult(
            ret=ret.logcat_pid, err=None if ret.error == "" else ret.error
        )

    @typechecked
    def stop_logcat_collect(self, device_id: str) -> GRPCResult:
        ret = self._execute_grpc_call(stop_logcat_collect, device_id)
        return GRPCResult(
            ret=ret.logcat_file, err=None if ret.error == "" else ret.error
        )

    @typechecked
    def run_last_installed_apk(
        self, device_id: str, execution_time: int = 10, custom_cmd: str = ""
    ) -> GRPCResult:
        ret = self._execute_grpc_call(
            run_last_installed_apk, device_id, execution_time, custom_cmd
        )
        return GRPCResult(err=None if ret.error == "" else ret.error)

    @typechecked
    def kill_app(self, device_id: str) -> GRPCResult:
        ret = self._execute_grpc_call(kill_app, device_id)
        return GRPCResult(err=None if ret.error == "" else ret.error)
