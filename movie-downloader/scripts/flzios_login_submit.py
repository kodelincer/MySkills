#!/usr/bin/env python3

import os
import sys
import json
import time
from pathlib import Path


HERMES_DIR = Path.home() / ".hermes"

RUNTIME_DIR = (
    HERMES_DIR
    / "runtime"
    / "movie-downloader"
)

STATE_FILE = (
    RUNTIME_DIR
    / "login_state.json"
)

ANSWER_FILE = (
    RUNTIME_DIR
    / "login_answer.json"
)

RESULT_FILE = (
    RUNTIME_DIR
    / "login_result.json"
)

PID_FILE = (
    RUNTIME_DIR
    / "login_pid.json"
)


def write_json(path: Path, data: dict):

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    os.replace(
        temp,
        path,
    )


def read_json(path: Path):

    try:

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return None


def process_alive(pid: int):

    try:

        os.kill(
            pid,
            0,
        )

        return True

    except (
        ProcessLookupError,
        ValueError,
    ):

        return False

    except PermissionError:

        return True


def main():

    if len(sys.argv) < 2:

        print(
            "Usage: flzios_login_submit.py <captcha>"
        )

        return 1


    captcha_answer = str(
        sys.argv[1]
    ).strip()


    if not captcha_answer:

        print(
            "INVALID_CAPTCHA_ANSWER"
        )

        return 1


    state = read_json(
        STATE_FILE
    )


    if not state:

        print(
            "LOGIN_SESSION_NOT_FOUND"
        )

        print(
            "Run flzios_login_start.py first."
        )

        return 1


    if (
        state.get("status")
        != "waiting_for_captcha"
    ):

        print(
            "LOGIN_NOT_WAITING_FOR_CAPTCHA"
        )

        print(
            f"STATE={state.get('status')}"
        )

        return 1


    session_id = state.get(
        "session_id"
    )


    if not session_id:

        print(
            "INVALID_LOGIN_SESSION"
        )

        return 1


    pid_info = read_json(
        PID_FILE
    )


    if not pid_info:

        print(
            "LOGIN_WORKER_NOT_RUNNING"
        )

        return 1


    pid = pid_info.get(
        "pid"
    )


    if (
        not pid
        or not process_alive(pid)
    ):

        print(
            "LOGIN_WORKER_NOT_RUNNING"
        )

        print(
            "Run flzios_login_start.py again."
        )

        return 1


    # Remove previous result if present.
    try:

        RESULT_FILE.unlink()

    except FileNotFoundError:

        pass


    # Send captcha to the SAME background worker
    # that owns the original Playwright page.
    write_json(
        ANSWER_FILE,
        {
            "session_id": session_id,
            "answer": captcha_answer,
            "created_at": time.time(),
        },
    )


    # Wait for login result.
    deadline = (
        time.monotonic()
        + 30
    )


    while time.monotonic() < deadline:

        result = read_json(
            RESULT_FILE
        )


        if (
            result
            and result.get("session_id")
            == session_id
        ):

            code = result.get(
                "code",
                "UNKNOWN",
            )


            if result.get("status"):

                print(
                    "LOGIN_SUCCESS"
                )

                print(
                    f"URL={result.get('url', '')}"
                )

                return 0


            print(
                "LOGIN_FAILED"
            )

            print(
                f"CODE={code}"
            )

            print(
                f"ERROR={result.get('error', '')}"
            )

            print(
                f"URL={result.get('url', '')}"
            )

            return 1


        time.sleep(0.2)


    print(
        "LOGIN_RESULT_TIMEOUT"
    )

    return 1


if __name__ == "__main__":

    sys.exit(
        main()
    )