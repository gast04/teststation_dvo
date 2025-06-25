# Mobile TestStation
An easy setup for running an Android application on multiple mobile devices and emulators.

# Backend REST API usage
Requests can be sent directly to the REST API of the backend, implemented using [FastAPI](https://fastapi.tiangolo.com/).

The following example uses the [Python requests](https://requests.readthedocs.io/en/latest/) library to fetch a
list of test devices available and then upload an .apk file to be run on a test device named 'emulator1' for 20 seconds.
```Python
import json
import requests

url = "http://<hub>:12001/getdevices/?arm64_only=false&arm32_only=false&amount=1"
r = requests.get(url=url)
devices = json.loads(r.content) # fetch list of test devices available

test_app = "<pathTo>/testo.apk"
url = "http://<hub>:12001/executeApp?execution_time=20&devices=emulator1"

with open(test_app, "rb") as f:
    r = requests.post(url, files={
            "file": ("testo.apk", f) # upload apk to be run on device 'emulator1' for 20 seconds
        }
    )
```

To keep the REST API simple, it only contains two endpoints:
* getdevices: list available devices (does not lock returned devices)
* executeApp: execute specified app on selected devices (if devices can be locked)

The REST API is automatically documented by FastAPI and can be viewed at `<hub>:12001/docs`. It is automatically shown on the [Streamlit](https://streamlit.io/) Web UI.

# Quick Startup Guide
The fastest way to get the TestStation up and running is to build `Dockerfile.frontend`
and run it. This automatically starts a Node, which connects the available devices and
the frontend with the RESTful API and the Web UI.

**Build steps:**
* `docker build -t mobile_teststation -f Dockerfile.base .` The base image is the same
  for Hub and Node. It contains all required dependencies for the application.
* `docker build --build-arg BASE_IMAGE=mobile_teststation -t mobile_teststation_frontend -f Dockerfile.frontend .`
  The frontend build takes the `mobile_teststation` base image and continues from there.

Once the frontend container has been built, it can be started using:
```sh
docker run --name mobile_ts_ui \
        --network host \
        --env=REL_DIR=/var/teststation/Release \
        --env=UI_PORT=12000 \
        --env=FASTAPI_IP=localhost \
        --env=FASTAPI_PORT=12001 \
        --env=GRPC_PORT=12002 \
        --env=GRPC_SERVER_ONE=localhost:12002 \
        --env=DOZZLE_WEBSITE=http://localhost:6060 \
        --env=KEYSTORE_FILE="test.jks" \
        --env=KEYSTORE_PASS="test_pass" \
        --env=KEYSTORE_KEY_ALIAS="thekey" \
        -v /tmp:/var/teststation/Release/ \
        -t mobile_teststation_frontend
```
Note: it is assumed that everything is running locally on a Linux machine.
Therefore localhost is used and `--network host`. If this is not the case and
for example the dozzle logger is running on a different machine, please change
the IP (and port).

Once the Docker container runs, the Web UI is available at `localhost:12000`, the RESTful
documentation (FastAPI) at `localhost:12001/docs`, and the Node grpc service
at `localhost:12002`.

The GRPC service can be tested/used via:
```Python
from src.grpc_wrapper.grpc_client import GRPCClient

grpc_client = GRPCClient(host="localhost", port=12002)
print(grpc_client.get_operating_system())

# get all available devices from the node
ret = grpc_client.get_adb_devices()
if ret.err is None:
    import json
    devices = json.loads(ret.ret)
    print(", ".join([r["id"] for r in devices]))
# >> emulator1, emulator2
```

The running Web UI will look like:

<img src="docs/web_ui.png" alt="drawing" width="70%"/>

The setup can be tested with the [helloWorld.apk](helloWorld.apk) application (already signed).
When a device is connected or an emulator started, the app can be started using the UI.
After the run, the logcat has to contain the logline `D TESTSTATION: this log should be visible`.

If multiple Nodes are started they can be simply added to the hub by adding an
extra environment variable starting with `GRPC_SERVER_` like
`--env=GRPC_SERVER_TWO=111.122.133.144:12345`. All environment variables starting with
this prefix will be checked and added to the internal Node's list.

The `KEYSTORE_*` variables have to be defined manually. Following the steps above,
you will not be able to sign an application. If the application is already
signed, the installation and execution process will work although signing is disabled.

# Node Startup
The Node image is based on the `mobile_teststation` image and can be build with
`docker build --build-arg BASE_IMAGE=mobile_teststation -t mobile_teststation_grpc -f Dockerfile.backend .`

This is needed if on a machine, different from the hub machine, devices/emulators are connected
which should also be accessible from the TestStation.

```sh
docker run --name mobile_ts_grpc \
        --network host \
        --env=REL_DIR=/var/teststation/Release \
        --env=KEYSTORE_FILE="test.jks" \
        --env=KEYSTORE_PASS="test_pass" \
        --env=KEYSTORE_KEY_ALIAS="thekey" \
        -v /tmp:/var/teststation/Release/ \
        -t mobile_teststation_grpc -p 12002
```

# Hub Startup 
The startup of the Hub is explained in [Quick-Startup-Guide](#-Quick-Startup-Guide).

If `--network host` is not specified (which is not possible on MacOS based systems), the used ports
need to be passed to the Docker config. The same is true for `ANDROID_ADB_SERVER_ADDRESS`, as explained in the
[technical report](https://denuvosoftwaresolutions.github.io/teststation/technical_description.html).

```sh
-p $GRPC_PORT:$GRPC_PORT \
-p $UI_PORT:$UI_PORT \
-p $FASTAPI_PORT:$FASTAPI_PORT \
--env=ANDROID_ADB_SERVER_ADDRESS=host.docker.internal \
```

# Android Tools
Needed Android tools:
* apksigner: to sign apk files
* bundletool: to convert aab files to apk
* aapt2: extract the package name from an apk

All needed tools are automatically fetched within the Dockerfile. Versions have
been tagged to:
* build-tools, Version: 35.0.0
* bundletool, Version 1.18.1 (latest available as of June 1, 2025)

# Dozzle Logger
All active container logs can be viewed via `<hub>:6060`.
The [dozzle](https://dozzle.dev/) logger has an agent running on each backend (needs to be started
manually). The agents can be connected to the main hub instance which runs on a (for example)
MacOS  machine.

**Agent startup command**
```
docker run -d \
    -p 8107:7007 \
    -v /var/run/docker.sock:/var/run/docker.sock \
    --name dozzle_logger \
    --restart unless-stopped \
    amir20/dozzle:latest agent
```

**MacOS startup command**
```
docker run -d -v /var/run/docker.sock:/var/run/docker.sock \
    -p 6060:8080 \
    --name dozzle_logger \
    --restart unless-stopped \
    amir20/dozzle:latest \
    --remote-agent <node_1_ip>:8107 \
    --remote-agent <node_2_ip>:8107
```

# Further reading

There are two articles:
* On the [Intrastructure and Architecture](https://denuvosoftwaresolutions.github.io/teststation/technical_description.html) of Mobile TestStation, and
* a guide on [How to uncover hidden bugs and boost test coverage](https://denuvosoftwaresolutions.github.io/teststation/use_case_description.html) using Mobile TestStation.

# License

The Mobile TestStation is copyright (c) 2025 Denuvo GmbH and licensed under [GPL-3](LICENSE).
The software is provided "as is". Licensor disclaims all warranties, express or implied, including, but 
not limited to, the implied warranties of merchantibility and fitness for a particular purpose.
