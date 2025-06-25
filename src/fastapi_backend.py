# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import asyncio
import argparse
import os
import sys
import json
import logging
import uvicorn
import shutil
import tempfile

from collections import defaultdict
from dotenv import load_dotenv
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

from fastapi import FastAPI, UploadFile, status, Depends, Query
from fastapi.exceptions import HTTPException

from grpc_wrapper.grpc_client import GRPCClient
from grpc_wrapper.app_executor import execute_app
from grpc_wrapper.utils.clogger import create_file_logger


if not load_dotenv():
    print("Could not load .env file!")
    sys.exit(-1)

if "REL_DIR" not in os.environ:
    print("Missing REL_DIR environment variable!")
    sys.exit(-1)

logger = create_file_logger(
    "log_fastapi.log", Path(os.environ["REL_DIR"]) / "Logs", "APILOG", logging.DEBUG
)


# Pydantic models for request/response validation
class DeviceInfo(BaseModel):
    id: str
    server_name: str
    in_use: bool
    state: str
    cpu_abi: str = Field(alias="ro.product.cpu.abi")
    product_model: str = Field(alias="ro.product.model")
    sdk: str = Field(alias="ro.build.version.sdk")
    release_version: str = Field(alias="ro.build.version.release")
    manufacturer: str = Field(alias="ro.product.manufacturer")


class DeviceResponse(BaseModel):
    devices: dict[str, DeviceInfo]


class ExecuteAppResponse(BaseModel):
    error: str | None = None
    result: str | None = None
    logcat_output: str | None = None


class ExecuteAppRequest(BaseModel):
    execution_time: int = Field(gt=0, description="Execution time in seconds")
    devices: str
    sign_app: bool = True
    custom_startup_cmd: str = ""

    @field_validator("devices")
    def validate_devices(cls, v):
        if not v:
            raise ValueError("Devices list cannot be empty")
        for dev in v.split(","):
            if "_" not in dev:
                raise ValueError(f"Invalid device format: {dev}")
            parts = dev.split("_")
            if len(parts) != 2:
                raise ValueError(f"Invalid device format: {dev}")
        return v


def init_grpc_clients() -> dict[str, GRPCClient]:
    """Initialize GRPC clients from environment variables.

    Scans environment variables starting with 'GRPC_SERVER_' to create GRPC clients.
    Each variable should be in the format 'GRPC_SERVER_NAME=HOST:PORT'.

    Returns:
        dict[str, GRPCClient]: Dictionary mapping server names to their GRPC clients.

    Raises:
        HTTPException: If no GRPC clients could be initialized or if connection to any server fails.
    """
    clients = {}
    for env_var in os.environ:
        if not env_var.startswith("GRPC_SERVER_"):
            continue

        server_name = env_var.split("_")[-1]
        value = os.environ[env_var]
        try:
            clients[server_name] = GRPCClient(
                host=value.split(":")[0],
                port=int(value.split(":")[1]),
            )
        except Exception as e:
            logger.error(
                f"Failed to initialize GRPC from Envvar '{env_var} ({os.environ[env_var]})': {str(e)}"
            )

    if not clients:
        logger.error(
            "No GRPC clients could be initialized. Check GRPC_SERVER_* environment variables."
        )
        raise ValueError("No GRPC clients initialized.")

    return clients


grpc_clients = init_grpc_clients()
app = FastAPI()


@app.get("/getdevices/", response_model=DeviceResponse)
async def getdevices(
    arm64_only: bool = Query(False),
    arm32_only: bool = Query(False),
    amount: int = Query(1),
    ids: str = Query(""),
) -> DeviceResponse:
    """Get available mobile devices across GRPC backends.

    Args:
        arm64_only: If True, only return arm64 devices.
        arm32_only: If True, only return arm32 devices.
        amount: Maximum number of devices to return. Must be greater than 0.
        ids: Comma-separated list of device IDs to filter by. Format: 'id1_server1,id2_server2'.

    Returns:
        DeviceResponse: Dictionary of available devices matching the criteria.

    Raises:
        HTTPException: If device ID parsing fails or if there's an error getting devices.
    """
    logger.info(f"API getdevices: {arm64_only}, {arm32_only}, {amount}, {ids}")

    requested_ids = defaultdict(list)
    if ids:
        try:
            for device_id in ids.split(","):
                device_id, server_name = device_id.split("_")
                requested_ids[server_name].append(device_id)
        except ValueError as e:
            logger.error(f"getdevices id parsing failed, '{device_id}'", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_406_NOT_ACCEPTABLE,
                detail=f"Invalid ID format: {str(e)}",
            )

    devices: dict[str, DeviceInfo] = {}
    try:
        for server_name, client in grpc_clients.items():
            ret = client.get_adb_devices()
            if ret.err is not None:
                logger.error(f"Failed to get devices from {server_name}: {ret.err}")
                continue

            for device in json.loads(ret.ret):
                device_info = DeviceInfo(server_name=server_name, **device)
                device_key = f"{device_info.id}_{server_name}"

                if requested_ids:
                    # if ids are requested, only return those if available
                    if (
                        server_name in requested_ids
                        and device_info.id in requested_ids[server_name]
                    ):
                        devices[device_key] = device_info
                    continue

                if len(devices) >= amount and not requested_ids:
                    return DeviceResponse(devices=devices)

                if arm64_only and device_info.cpu_abi == "arm64-v8a":
                    devices[device_key] = device_info
                elif arm32_only and device_info.cpu_abi == "armeabi-v7a":
                    devices[device_key] = device_info
                elif not arm64_only and not arm32_only:
                    devices[device_key] = device_info

    except Exception as e:
        logger.error("getdevices failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get devices: {str(e)}",
        )

    return DeviceResponse(devices=devices)


@app.post("/executeApp", response_model=dict[str, ExecuteAppResponse])
async def executeApp(
    file: UploadFile,
    request: ExecuteAppRequest = Depends(),
) -> dict[str, ExecuteAppResponse]:
    """Execute an application on selected mobile devices.

    Args:
        file: The APK/AAB file to execute.
        request: ExecuteAppRequest containing execution parameters:
            - execution_time: Duration to run the app in seconds
            - devices: Comma-separated list of device IDs
            - sign_app: Whether to sign the app before installation
            - custom_startup_cmd: Custom ADB command to start the app

    Returns:
        dict[str, ExecuteAppResponse]: Dictionary mapping device IDs to their execution results.
        Each result contains:
            - error: Error message if execution failed
            - result: Execution result message
            - logcat_output: Full logcat output from the device

    Raises:
        HTTPException: If file upload fails, no devices are available, or execution fails.
    """
    logger.info(f"API executeApp: {file.filename}, {request}")

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No file provided"
        )

    if Path(file.filename).suffix not in [".apk", ".aab"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .apk/.aab files supported!",
        )

    devs_per_server = defaultdict(list)
    for dev in request.devices.split(","):
        devid, server_name = dev.split("_")
        devs_per_server[server_name.strip()].append(devid.strip())

    locked_devices = defaultdict(list)
    for server_name in devs_per_server:
        res = grpc_clients[server_name].get_free_device(
            device_list=devs_per_server[server_name]
        )
        if res.err is not None:
            logger.error(f"Failed to get free devices from {server_name}: {res.err}")
            continue
        locked_devices[server_name].extend(res.ret)

    if not any(locked_devices.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not lock any device",
        )

    stored_file = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=Path(file.filename).suffix
        ) as buffer:
            shutil.copyfileobj(file.file, buffer)
            stored_file = Path(buffer.name)
    except Exception as e:
        logger.error("File Upload failed", exc_info=True)
        # Unlock devices in error case
        for server_name, devids in locked_devices.items():
            for did in devids:
                try:
                    grpc_clients[server_name].unlock_device(did)
                except Exception as unlock_error:
                    logger.error(f"Failed to unlock device {did}: {str(unlock_error)}")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error uploading file: {str(e)}",
        )

    try:
        return_dict = {}
        exec_funcs = []
        for server_name, devids in locked_devices.items():
            # Upload file to grpc_server
            logger.info(f"AppFile Upload: {stored_file}")
            ret = grpc_clients[server_name].upload_app(stored_file)
            if ret.err is not None:
                logger.error(f"Failed to upload app to {server_name}: {ret.err}")
                for d in locked_devices[server_name]:
                    return_dict[f"{d}_{server_name}"] = ExecuteAppResponse(
                        error="File Upload to grpc backend failed!"
                    )
                continue

            logger.info(f"AppFile Stored: {ret.ret}")
            for did in devids:
                exec_funcs.append(
                    execute_app(
                        did,
                        Path(ret.ret),
                        grpc_client=grpc_clients[server_name],
                        server_name=server_name,
                        execution_time=request.execution_time,
                        sign_app=request.sign_app,
                        custom_cmd=request.custom_startup_cmd,
                    )
                )

        execution_results = await asyncio.gather(*exec_funcs)

        for exec_res in execution_results:
            response = ExecuteAppResponse(
                error=str(exec_res.error) if exec_res.error else None,
                result=exec_res.result,
            )

            if isinstance(exec_res.logcat_file, Path):
                try:
                    response.logcat_output = exec_res.logcat_file.read_text()
                except Exception as e:
                    logger.error(f"Failed to read logcat file: {str(e)}")
                    response.error = f"Failed to read logcat: {str(e)}"

            return_dict[f"{exec_res.devid}_{exec_res.server_name}"] = response

        return return_dict

    finally:
        # Cleanup temporary file
        if stored_file and stored_file.exists():
            try:
                stored_file.unlink()
            except Exception as e:
                logger.error(f"Failed to delete temporary file: {str(e)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FastAPI backend for mobile teststation."
    )
    parser.add_argument("-p", "--port", default=9001)
    args = parser.parse_args()
    port = int(args.port)
    uvicorn.run(app, host="0.0.0.0", port=port)
