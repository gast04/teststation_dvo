# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import asyncio
import tempfile

from dataclasses import dataclass
from pathlib import Path

from .grpc_client import GRPCClient
from .utils.device_context import DeviceUsage


@dataclass
class ExecutionResult:
    error: bool
    devid: str
    result: str
    logcat_file: Path | None = None
    server_name: str | None = None


async def execute_app(
    devid: str,
    stored_file: Path,
    grpc_client: GRPCClient,
    server_name: str,
    execution_time: int,
    sign_app: bool = True,
    custom_cmd: str = "",
) -> ExecutionResult:
    """
    Expects a locked device id.
    """

    exec_result = ExecutionResult(
        error=True, devid=devid, server_name=server_name, result=""
    )

    # context manager to ensure device clean up
    with DeviceUsage(grpc_client=grpc_client, device_id=devid):
        try:
            # DeviceUsage is safety to ensure app is uninstalled in case of error
            response = grpc_client.start_logcat_collect(devid)
            if response.err is not None:
                exec_result.result = f"Error start_logcat_collect: {response.err}"
                return exec_result

            # NOTE: adb doesn't like parallel execution, probably due to port usage
            # adb execution is lock guarded per server
            # response = await asyncio.to_thread(
            #    grpc_client.install_apk, devid, stored_file, sign_app=True
            # )
            response = grpc_client.install_app(devid, stored_file, sign_app=sign_app)
            if response.err is not None:
                exec_result.result = f"Error install_app: {response.err}"
                return exec_result

            response = await asyncio.to_thread(
                grpc_client.run_last_installed_apk, devid, execution_time, custom_cmd
            )
            if response.err is not None:
                exec_result.result = response.err
                return exec_result

            response = await asyncio.to_thread(grpc_client.uninstall_app, devid)
            if response.err is not None:
                exec_result.result = f"Error uninstall_app: {response.err}"
                return exec_result

            exec_result.error = False
            return exec_result

        finally:
            response = grpc_client.stop_logcat_collect(devid)
            if response.err is not None or response.ret is None:
                exec_result.error = True
                exec_result.result = f"Error stopping logcat: {response.err}"
                return exec_result

            # pull logcat file from remote
            result = grpc_client.pull_file(Path(response.ret))
            if result.err is not None or result.ret is None:
                exec_result.error = True
                exec_result.result = f"Error pulling logcat: {response.err}"
                return exec_result

            # store file locally as tempfile
            with tempfile.NamedTemporaryFile(delete=False) as tf:
                logcat_path = Path(tf.name)
                logcat_path.write_text(result.ret)

            # everything passed
            exec_result.logcat_file = logcat_path
