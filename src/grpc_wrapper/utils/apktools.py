# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import os
import subprocess
from pathlib import Path
from typing import Any
from typeguard import typechecked

# Constants
TOOLS_PATH: Path = Path(__file__).parent.parent / "tools"
AAPT2_PATH: Path = TOOLS_PATH / "build-tools/35.0.0/aapt2"
APKSIGNER_PATH: Path = TOOLS_PATH / "build-tools/35.0.0/apksigner"
BUNDLETOOL_PATH: Path = TOOLS_PATH / "bundletool.jar"


@typechecked
def eprint(msg: str, logger: Any = None) -> None:
    """Print error message to logger or stdout."""
    if logger is not None:
        logger.error(msg)
    else:
        print(msg)


def get_ks_args(logger: Any = None) -> list[str] | None:
    if "KEYSTORE_PASS" not in os.environ:
        eprint("KEYSTORE_PASS environment variable not set", logger=logger)
        return None

    if "KEYSTORE_KEY_ALIAS" not in os.environ:
        eprint("KEYSTORE_KEY_ALIAS environment variable not set", logger=logger)
        return None

    if "KEYSTORE_FILE" not in os.environ:
        eprint("KEYSTORE_FILE environment variable not set", logger=logger)
        return None

    keystore_file = Path(__file__).parent.parent / "tools" / os.environ["KEYSTORE_FILE"]
    if not keystore_file.exists():
        eprint(
            f"Signing Failed: Keyfile not! {keystore_file.as_posix()}", logger=logger
        )
        return None

    return [
        "--ks",
        keystore_file.as_posix(),
        "--ks-pass",
        f"pass:{os.environ['KEYSTORE_PASS']}",
        "--ks-key-alias",
        os.environ["KEYSTORE_KEY_ALIAS"],
    ]


@typechecked
def get_package_name(apk_path: Path, logger: Any = None) -> str | None:
    """Extract package name from APK using aapt2.

    Args:
        apk_path: Path to APK file
        logger: Optional logger for error messages

    Returns:
        Package name if found, None on error
    """
    try:
        cmd = [
            AAPT2_PATH.as_posix(),
            "dump",
            "badging",
            apk_path.as_posix(),
        ]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as p:
            assert p.stdout is not None, "AAPT BIN stdout is None"
            out = p.stdout.readlines()
            for line in out:
                if b"package: name=" in line:
                    return (
                        line.decode("utf-8", errors="replace")
                        .split("name='")[1]
                        .split("'")[0]
                    )
            return ""

    except Exception as e:
        eprint(f"Error getting package name: {str(e)}", logger=logger)
        return None


@typechecked
def zipalign_apk(apk_path: Path, logger: Any = None) -> Path | None:
    """Align APK using zipalign.

    Args:
        apk_path: Path to APK file
        logger: Optional logger for error messages

    Returns:
        Path to aligned APK if successful, None on error
    """
    try:
        aligned_apk = apk_path.parent / f"{apk_path.stem}_aligned.apk"
        cmd = [
            "zipalign",
            "-p",
            "4",
            apk_path.as_posix(),
            aligned_apk.as_posix(),
        ]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
            _, stderr = p.communicate()
            if stderr:
                eprint(stderr.decode("utf-8", errors="replace"), logger=logger)
        return aligned_apk
    except Exception as e:
        eprint(f"Error zipalign apk: {str(e)}", logger=logger)
        return None


@typechecked
def sign_apk(
    apk_path: Path, with_aligning: bool = False, logger: Any = None
) -> Path | None:
    """Sign APK using apksigner.

    Args:
        apk_path: Path to APK file
        with_aligning: Whether to align APK before signing
        logger: Optional logger for error messages

    Returns:
        Path to signed APK if successful, None on error
    """
    try:
        if with_aligning:
            if (aligned_path := zipalign_apk(apk_path)) is None:
                return None
            sign_file = aligned_path
            signed_apk = apk_path.parent / aligned_path.name.replace(
                "aligned.apk", "signed.apk"
            )
        else:
            sign_file = apk_path
            signed_apk = apk_path.parent / apk_path.name.replace(".apk", "_signed.apk")

        if (ks_args := get_ks_args(logger=logger)) is None:
            return None

        cmd = [
            APKSIGNER_PATH.as_posix(),
            "sign",
            *ks_args,
            "--out",
            signed_apk.as_posix(),
            sign_file.as_posix(),
        ]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
            _, stderr = p.communicate()
            if stderr:
                eprint(stderr.decode("utf-8", errors="replace"), logger=logger)

        if not signed_apk.exists():
            eprint(f"Error signing apk: {signed_apk} not found", logger=logger)
            return None

        return signed_apk
    except Exception as e:
        eprint(f"Error signing apk: {str(e)}", logger=logger)
        return None


@typechecked
def aab_to_apk(aab_path: Path, logger: Any = None) -> Path | None:
    """Convert Android App Bundle (AAB) to APK using bundletool.

    Args:
        aab_path: Path to AAB file
        logger: Optional logger for error messages

    Returns:
        Path to converted APK if successful, None on error
    """
    if (ks_args := get_ks_args(logger=logger)) is None:
        return None

    apks_path = aab_path.as_posix().replace(".aab", ".apks")
    try:
        cmd = [
            "java",
            "-jar",
            BUNDLETOOL_PATH.as_posix(),
            "build-apks",
            "--bundle",
            aab_path.as_posix(),
            "--output",
            apks_path,
            "--mode",
            "universal",
            *ks_args,
        ]

        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
            _, error = p.communicate()

        if error:
            eprint(
                f"Error running bundle tool: {error.decode('utf-8', errors='replace')}",
                logger=logger,
            )
            return None

    except Exception as e:
        eprint(f"Error converting aab to apk: {str(e)}", logger=logger)
        return None

    try:
        output_dir = aab_path.parent / aab_path.stem
        cmd = [
            "unzip",
            "-o",
            apks_path,
            "-d",
            output_dir.as_posix(),
        ]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
            _, error = p.communicate()

        if error:
            eprint(
                f"Error unzip .apks: {error.decode('utf-8', errors='replace')}",
                logger=logger,
            )
            return None

    except Exception as e:
        eprint(f"Error unzipping .apks file! {str(e)}", logger=logger)
        return None

    output_apk = output_dir / "universal.apk"
    if not output_apk.exists():
        eprint("Error output apk file does not exist!", logger=logger)
        return None

    return output_apk
