# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import asyncio
import json
import logging
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv
from typeguard import typechecked

from grpc_wrapper.app_executor import execute_app
from grpc_wrapper.grpc_client import GRPCClient
from grpc_wrapper.utils.clogger import create_file_logger

# Constants
UI_FILE_UPLOAD_DIR = "UIFileUpload"
DEBUG_MODE = "DEBUG_MODE" in st.query_params

# Version handling
ts_version = Path(__file__).parent.parent / "ts_version.txt"
CURRENT_VERSION = (
    f"Version Tag: {ts_version.read_text()}" if ts_version.exists() else "NO VERSION"
)

# Streamlit configuration
st.set_page_config(
    page_title="Teststation",
    page_icon=".streamlit/test.png",
    initial_sidebar_state="collapsed",
    layout="wide",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": f"""{CURRENT_VERSION}

For Mobile Devs and CI usage only!
If you are from QA, turn around, and build your own!""",
    },
)

# Environment setup
if not load_dotenv():
    st.error("Could not load .env file!")
    st.stop()

REL_DIR = Path(os.environ["REL_DIR"])
logger = create_file_logger("log_ui.log", REL_DIR / "Logs", "UILOG", logging.DEBUG)

# Display backend information
fapi_host = f"{os.environ['FASTAPI_IP']}:{os.environ['FASTAPI_PORT']}"
st.markdown(
    f"**FastAPI backend available at:** [{fapi_host}/docs](http://{fapi_host}/docs)"
)

if "DOZZLE_WEBSITE" in os.environ:
    st.markdown(
        f"**Dozzle Logger available at:** [docker logs]({os.environ['DOZZLE_WEBSITE']})"
    )

st.title("Upload and Run")
st.session_state.exec_time = 20  # DEFAULT execution time


@dataclass
class ExecutionResult:
    """Result of an app execution on a device."""

    error: bool
    devid: str
    result: str
    logcat_file: Path | None = None
    server_name: str | None = None


class Connections:
    """Manages connections to GRPC servers and their devices."""

    def __init__(self, clients: dict[str, GRPCClient]):
        self.clients = clients
        self.table_devices: dict[str, list[dict[str, Any]]] = {}

    @classmethod
    def create(cls) -> "Connections | None":
        """Create a new Connections instance.

        Returns:
            Optional[Connections]: New instance if clients were created, None otherwise.
        """
        clients = create_grpc_clients()
        if not clients:
            return None
        return cls(clients=clients)

    def print_device_table(self) -> None:
        """Display a table of available devices and their status."""
        self.table_devices = {}
        display_table = []

        for server_name, client in self.clients.items():
            ret = client.get_adb_devices()
            if ret.err is not None:
                continue

            self.table_devices[server_name] = json.loads(ret.ret)

            for device in self.table_devices[server_name]:
                device["name"] = f"{device['id']}_{server_name}"

                status = (
                    "InUse"
                    if device["in_use"]
                    else "Ready"
                    if device["state"] == "device"
                    else "Not Usable"
                )

                entry = {
                    "Status": status,
                    "Arch": device["ro.product.cpu.abi"],
                    "Typ": f"{device['ro.product.model']} ({device['ro.product.manufacturer']})",
                    "OS/SDK": f"{device['ro.build.version.release'].rjust(2, '_')}/{device['ro.build.version.sdk']}",
                    "Name (id_server)": device["name"],
                }
                display_table.append(entry)

        st.markdown("**Available Devices/Emulators**")
        st.table(display_table)

    def available_devices(self) -> list[str]:
        """Get list of available (not in use) devices.

        Returns:
            list[str]: List of available device names.
        """
        return [
            device["name"]
            for devices in self.table_devices.values()
            for device in devices
            if not device["in_use"]
        ]

    @typechecked
    def get_client_by_server(self, server_name: str) -> GRPCClient | None:
        """Get GRPC client for a specific server.

        Args:
            server_name: Name of the server to get client for.

        Returns:
            GRPCClient | None: Client if server exists, None otherwise.
        """
        return self.clients.get(server_name)


def create_grpc_clients(with_logs: bool = True) -> dict[str, GRPCClient]:
    """Create GRPC clients from environment variables.

    Args:
        with_logs: Whether to display connection status in UI.

    Returns:
        dict[str, GRPCClient]: Dictionary of server names to their clients.
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
            if with_logs:
                st.success(f"GRPC_Server: {server_name} ({value})")
        except Exception as e:
            if with_logs:
                st.error(f"Could not connect to: {server_name} ({value})")
            logger.error(f"Failed to connect to {server_name}: {str(e)}", exc_info=True)

    return clients


def create_sidebar() -> None:
    """Create and handle the sidebar UI elements."""
    st.sidebar.title("Settings")

    exec_time = st.sidebar.text_input("App Execution Time", value="20", max_chars=3)
    if exec_time is None:
        return

    st.sidebar.write(
        ":warning: If a debug app is uploaded and no 'dvo_' prints are shown, "
        "try increasing the execution time. (10seconds is not enough on all devices)"
    )

    logger.info(f"Exec_time: {exec_time}")

    if not str(exec_time).isdigit():
        st.sidebar.error("Exectime has to be Integer [1 - 999]")
        return

    exec_time = int(exec_time)
    if not 1 <= exec_time <= 999:
        st.sidebar.error("Exectime has to be Integer [1 - 999]")
        return

    st.session_state.exec_time = exec_time

    st.sidebar.title("Note")
    st.sidebar.write(
        ":point_up: Running arm packages on x86 emulators have the known issue that "
        "telemetry will not work, with the error `CHECK failed: HostPlatform::kHasAES`. "
        "The emulator does not support these instructions"
    )

    if DEBUG_MODE:
        st.sidebar.title("Debug Features")
        if st.sidebar.button("Free all Devices"):
            clients = create_grpc_clients(with_logs=False)
            for client in clients.values():
                client.unlock_device("__ALL__")


def app_upload() -> str | None:
    """Handle app file upload.

    Returns:
        Optional[str]: Path to uploaded file if successful, None otherwise.
    """
    uploaded_file = st.file_uploader("Choose a file", type=[".apk", ".aab"])
    if uploaded_file is None:
        return None

    if not os.path.exists(UI_FILE_UPLOAD_DIR):
        os.makedirs(UI_FILE_UPLOAD_DIR)

    # Multiuser issue, same filename...
    zip_path = os.path.join(os.getcwd(), UI_FILE_UPLOAD_DIR, uploaded_file.name)
    with open(zip_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    st.write(f"'{uploaded_file.name}' has been saved.")
    return zip_path


@st.dialog("Logcat Viewer")
def btn_logcat_show(device_id: str, logcat_output: str) -> None:
    """Display logcat output in a dialog.

    Args:
        device_id: ID of the device.
        logcat_output: Logcat output to display.
    """
    st.write(f"Logcat for Device: {device_id}")
    st.write(logcat_output)


@st.fragment()
def display_execution_result() -> None:
    """Display execution results in the UI."""
    if "exec_results" not in st.session_state:
        return

    st.markdown("**Execution Results:**")
    for exec_res in st.session_state.exec_results:
        cols = st.columns([1, 0.3, 2])

        cols[0].write(f"{exec_res.devid}_{exec_res.server_name}")
        cols[1].write("NG" if exec_res.error else "OK")

        if exec_res.error:
            cols[2].write(exec_res.result)

        if exec_res.logcat_file:
            cols[2].download_button(
                "Download Logcat",
                key=f"btn_dl_{exec_res.devid}_{random.randint(0, 1337)}",
                data=exec_res.logcat_file.read_text(),
                file_name=f"{exec_res.devid}_{exec_res.server_name}.logcat",
            )


def get_available_devices(connections: Connections) -> list[str]:
    """Get list of available devices.

    Args:
        connections: Connections instance to get devices from.

    Returns:
        list[str]: List of available device names.

    NOTE: this is a bit of a bottleneck, as reloading takes time (seconds)
    """
    return connections.available_devices()


async def execution_loop(
    connections: Connections,
    app_file: str,
    locked_devices: dict[str, list[str]],
    sign_app: bool,
) -> None:
    """Execute apps on selected devices.

    Args:
        connections: Connections instance to use.
        app_file: Path to app file to execute.
        locked_devices: Dictionary of server names to their locked device IDs.
        sign_app: Whether to sign the app before installation.
    """
    exec_funcs = []

    for server_name, devids in locked_devices.items():
        client = connections.get_client_by_server(server_name)
        if client is None:
            logger.error(f"Connection to {server_name} FAILED!")
            continue

        # Upload file to grpc_server
        logger.info(f"AppFile Upload: {app_file}")
        ret = client.upload_app(Path(app_file))
        if ret.err is not None:
            st.error(f"App Upload to {server_name} FAILED! `{ret.err}`")
            continue

        assert ret.ret is not None
        logger.info(f"AppFile Stored: {ret.ret}")
        stored_file = Path(ret.ret)

        for devid in devids:
            exec_funcs.append(
                execute_app(
                    devid,
                    stored_file,
                    grpc_client=client,
                    server_name=server_name,
                    execution_time=st.session_state.exec_time,
                    sign_app=sign_app,
                )
            )

    execution_results = await asyncio.gather(*exec_funcs)
    st.session_state.exec_results = execution_results


def main() -> None:
    """Main application entry point."""
    create_sidebar()

    connections = Connections.create()
    if connections is None:
        st.error("Could not create Connections to Teststations!")
        st.stop()
        return

    connections.print_device_table()

    app_file = app_upload()
    if app_file is None:
        return

    logger.info("available devices")
    av_devices = get_available_devices(connections)

    selected_devices = st.multiselect(
        "Available Emulators",
        placeholder="Choose Emulator",
        options=av_devices,
        max_selections=3,
    )

    logger.info(f"Devices selected: {selected_devices}")
    if not selected_devices:
        return

    cols = st.columns([1, 1, 3])
    with cols[0]:
        run_protection = st.button(
            "Run app", key="btn_runapp", help="Run app(s) on selected devices"
        )

    with cols[1]:
        sign_app = st.checkbox("sign with test1.jks", value=True)

    if not run_protection:
        return

    st.write("Locking devices...")

    devs_per_server = defaultdict(list)
    for sdevice in selected_devices:
        server_name = sdevice.split("_")[-1]
        dev_id = "".join(sdevice.split("_")[:-1])
        devs_per_server[server_name].append(dev_id)

    locked_devices = defaultdict(list)
    for server_name in devs_per_server:
        client = connections.get_client_by_server(server_name)
        if client is None:
            continue

        ret = client.get_free_device(device_list=devs_per_server[server_name])
        if ret.err is not None:
            st.error("Could not lock devices!")
            st.stop()

        assert ret.ret is not None
        locked_devices[server_name] = ret.ret
        st.write(
            f"Locked device(s): {server_name} {', '.join(ld for ld in locked_devices[server_name])}"
        )
    st.write(f"Run Application on emulator(s). Exectime: {st.session_state.exec_time}")

    with st.spinner("Execute app(s)..."):
        asyncio.run(execution_loop(connections, app_file, locked_devices, sign_app))
    display_execution_result()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        st.error(str(error))
        logger.error("Application error", exc_info=True)
