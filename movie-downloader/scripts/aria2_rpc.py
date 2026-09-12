#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import time
import urllib.request


# ============================================================
# CONFIGURATION
# ============================================================

RPC_URL = os.getenv(
    "ARIA2_RPC_URL",
    "http://127.0.0.1:6800/jsonrpc",
)

RPC_SECRET = os.getenv(
    "ARIA2_RPC_SECRET",
    "myStrongAria2Secret",
)

DOWNLOAD_DIR = os.path.expanduser(
    os.getenv(
        "ARIA2_DOWNLOAD_DIR",
        "~/Downloads/Movies",
    )
)


# ============================================================
# RPC
# ============================================================

def rpc(method, params=None):

    if params is None:
        params = []

    rpc_params = []

    if RPC_SECRET:
        rpc_params.append(
            f"token:{RPC_SECRET}"
        )

    rpc_params.extend(params)

    payload = {
        "jsonrpc": "2.0",
        "id": "hermes",
        "method": method,
        "params": rpc_params,
    }

    req = urllib.request.Request(
        RPC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json"
        },
        method="POST",
    )

    with urllib.request.urlopen(
        req,
        timeout=5,
    ) as response:

        result = json.loads(
            response.read().decode("utf-8")
        )

    if "error" in result:
        raise RuntimeError(
            result["error"]
        )

    return result.get("result")


# ============================================================
# SERVER
# ============================================================

def is_running():

    try:

        rpc(
            "aria2.getVersion"
        )

        return True

    except Exception:

        return False


def start():

    if is_running():

        print(
            "aria2 RPC is already running."
        )

        return True


    os.makedirs(
        DOWNLOAD_DIR,
        exist_ok=True,
    )


    command = [
        "aria2c",

        "--enable-rpc=true",

        "--rpc-listen-all=false",

        "--rpc-listen-port=6800",

        "--continue=true",

        "--max-connection-per-server=8",

        "--split=8",

        "--min-split-size=1M",

        "--file-allocation=none",

        f"--dir={DOWNLOAD_DIR}",
    ]


    if RPC_SECRET:

        command.append(
            f"--rpc-secret={RPC_SECRET}"
        )


    subprocess.Popen(
        command,

        stdout=subprocess.DEVNULL,

        stderr=subprocess.DEVNULL,

        start_new_session=True,
    )


    for _ in range(20):

        time.sleep(0.25)

        if is_running():

            print(
                "aria2 RPC started."
            )

            return True


    raise RuntimeError(
        "aria2 RPC failed to start."
    )


# ============================================================
# ADD DOWNLOAD
# ============================================================

def add(
    url,
    filename=None,
    headers=None,
):

    """
    Add a download to aria2.

    headers:
        optional list such as:

        [
            "Referer: https://flzios.com/...",
            "Cookie: PHPSESSID=...",
            "User-Agent: Mozilla/5.0 ..."
        ]

    Returns:
        aria2 GID
    """

    if not is_running():
        start()


    os.makedirs(
        DOWNLOAD_DIR,
        exist_ok=True,
    )


    options = {

        "dir": DOWNLOAD_DIR,

        "continue": "true",
    }


    if filename:

        options["out"] = filename


    if headers:

        options["header"] = headers


    gid = rpc(
        "aria2.addUri",
        [
            [url],
            options,
        ],
    )


    return gid


# ============================================================
# STATUS
# ============================================================

def get_status(gid):

    result = rpc(
        "aria2.tellStatus",

        [
            gid,

            [
                "gid",
                "status",
                "totalLength",
                "completedLength",
                "downloadSpeed",
                "errorCode",
                "errorMessage",
                "files",
            ],
        ],
    )


    total = int(
        result.get(
            "totalLength",
            0,
        )
    )


    completed = int(
        result.get(
            "completedLength",
            0,
        )
    )


    speed = int(
        result.get(
            "downloadSpeed",
            0,
        )
    )


    percent = (
        completed / total * 100
        if total
        else 0
    )


    remaining = max(
        total - completed,
        0,
    )


    eta = (
        remaining / speed
        if speed > 0
        else None
    )


    return {

        "gid": gid,

        "status":
            result.get("status"),

        "percent":
            round(percent, 1),

        "completed_bytes":
            completed,

        "total_bytes":
            total,

        "speed_bytes_sec":
            speed,

        "eta_seconds":
            round(eta)
            if eta is not None
            else None,

        "error_code":
            result.get("errorCode"),

        "error_message":
            result.get("errorMessage"),

        "files":
            result.get("files", []),
    }


def status(gid):

    result = get_status(
        gid
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )

    return result


# ============================================================
# DOWNLOAD CONTROL
# ============================================================

def pause(gid):

    rpc(
        "aria2.pause",
        [gid],
    )

    print(
        f"Paused: {gid}"
    )


def resume(gid):

    rpc(
        "aria2.unpause",
        [gid],
    )

    print(
        f"Resumed: {gid}"
    )


def cancel(gid):

    try:

        rpc(
            "aria2.remove",
            [gid],
        )

    except Exception:

        # Completed/stopped downloads may need forceRemove.
        rpc(
            "aria2.forceRemove",
            [gid],
        )


    print(
        f"Cancelled: {gid}"
    )


def stop():

    if not is_running():

        print(
            "aria2 RPC is not running."
        )

        return


    rpc(
        "aria2.shutdown"
    )


    print(
        "aria2 RPC shutdown requested."
    )


# ============================================================
# CLI
# ============================================================

def main():

    if len(sys.argv) < 2:

        print(
            "Usage: aria2_rpc.py "
            "start|add|status|pause|resume|cancel|stop"
        )

        sys.exit(1)


    command = sys.argv[1]


    if command == "start":

        start()


    elif command == "add":

        if len(sys.argv) < 3:

            print(
                "Usage: aria2_rpc.py add URL [FILENAME]"
            )

            sys.exit(1)


        url = sys.argv[2]

        filename = (
            sys.argv[3]
            if len(sys.argv) >= 4
            else None
        )


        gid = add(
            url,
            filename,
        )


        print(gid)


    elif command == "status":

        if len(sys.argv) < 3:
            sys.exit(
                "Missing GID"
            )

        status(
            sys.argv[2]
        )


    elif command == "pause":

        pause(
            sys.argv[2]
        )


    elif command == "resume":

        resume(
            sys.argv[2]
        )


    elif command == "cancel":

        cancel(
            sys.argv[2]
        )


    elif command == "stop":

        stop()


    else:

        print(
            f"Unknown command: {command}"
        )

        sys.exit(1)


if __name__ == "__main__":

    main()