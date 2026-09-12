#!/usr/bin/env python3

import os
import sys
import json
import time
import uuid
import signal
import asyncio
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


# ============================================================
# Paths
# ============================================================

HERMES_DIR = Path.home() / ".hermes"

ENV_FILE = HERMES_DIR / "secrets" / "movie-site.env"

RUNTIME_DIR = HERMES_DIR / "runtime" / "movie-downloader"

PROFILE_DIR = HERMES_DIR / "profiles" / "flzios"

STATE_FILE = RUNTIME_DIR / "login_state.json"

ANSWER_FILE = RUNTIME_DIR / "login_answer.json"

RESULT_FILE = RUNTIME_DIR / "login_result.json"

PID_FILE = RUNTIME_DIR / "login_pid.json"

LOG_FILE = RUNTIME_DIR / "login_worker.log"

CAPTCHA_FILE = RUNTIME_DIR / "flzios_captcha.png"

AUTH_STATE_FILE = (
    HERMES_DIR
    / "runtime"
    / "movie-downloader"
    / "flzios_auth_state.json"
)


DEFAULT_LOGIN_URL = "https://flzios.com/login"

CAPTCHA_TIMEOUT_SECONDS = 300


RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Helpers
# ============================================================

def write_json(path: Path, data: dict):
    """
    Atomic JSON write so another process never reads
    a partially-written file.
    """

    temp = path.with_suffix(path.suffix + ".tmp")

    temp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    os.replace(temp, path)


def read_json(path: Path):
    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return None


def process_alive(pid: int) -> bool:

    try:
        os.kill(pid, 0)
        return True

    except (ProcessLookupError, ValueError):
        return False

    except PermissionError:
        return True


def current_worker():

    info = read_json(PID_FILE)

    if not info:
        return None

    pid = info.get("pid")

    if not pid:
        return None

    if process_alive(pid):
        return info

    return None


def cleanup_runtime():

    for path in [
        STATE_FILE,
        ANSWER_FILE,
        RESULT_FILE,
        CAPTCHA_FILE,
    ]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def cleanup_stale_profile_locks():
    """
    Chromium may leave profile lock files after an abnormal crash.
    Only call this when no worker process is alive.
    """

    for name in [
        "SingletonLock",
        "SingletonSocket",
        "SingletonCookie",
    ]:

        path = PROFILE_DIR / name

        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except Exception:
            pass


# ============================================================
# Background login worker
# ============================================================

async def login_worker(session_id: str):

    load_dotenv(ENV_FILE)

    # Support both your NEW names and your OLD FilmazClient names.
    username = (
        os.getenv("MOVIE_SITE_USERNAME")
        or os.getenv("flzios_user")
    )

    password = (
        os.getenv("MOVIE_SITE_PASSWORD")
        or os.getenv("flzios_pass")
    )

    login_url = (
        os.getenv("LOGIN_URL")
        or os.getenv("login_url")
        or DEFAULT_LOGIN_URL
    )

    if not username or not password:

        write_json(
            RESULT_FILE,
            {
                "session_id": session_id,
                "status": False,
                "code": "MISSING_CREDENTIALS",
                "error": (
                    "MOVIE_SITE_USERNAME / MOVIE_SITE_PASSWORD "
                    "not found"
                ),
            },
        )

        return


    playwright = None
    context = None
    page = None


    try:

        playwright = await async_playwright().start()


        # Persistent profile means successful login cookies survive
        # after this worker exits.
        #
        # headless=True = NO visible Chrome/Chromium window.
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),

            headless=True,

            viewport={
                "width": 1366,
                "height": 768,
            },

            ignore_https_errors=True,
        )


        page = await context.new_page()


        # --------------------------------------------------------
        # OPEN LOGIN PAGE
        # --------------------------------------------------------

        await page.goto(
            login_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )


        # --------------------------------------------------------
        # WAIT FOR AJAX LOGIN FORM
        # --------------------------------------------------------
        #
        # Your page initially contains:
        #
        #   <div id="result"></div>
        #
        # and AJAX injects the login form later.
        #
        # Therefore waiting for DOMContentLoaded is NOT enough.
        # input[name="mobile"] is our synchronization point.
        # --------------------------------------------------------

        await page.locator(
            'input[name="mobile"]'
        ).wait_for(
            state="visible",
            timeout=15000,
        )


        await page.locator(
            'input[name="password"]'
        ).wait_for(
            state="visible",
            timeout=15000,
        )


        await page.locator(
            "#imgCap"
        ).wait_for(
            state="visible",
            timeout=15000,
        )


        # Make sure captcha image data has actually finished loading.
        await page.wait_for_function(
            """
            () => {
                const img = document.querySelector('#imgCap');

                return (
                    img &&
                    img.complete &&
                    img.naturalWidth > 0 &&
                    img.naturalHeight > 0
                );
            }
            """,
            timeout=15000,
        )


        # --------------------------------------------------------
        # FILL USERNAME + PASSWORD
        # --------------------------------------------------------

        await page.locator(
            'input[name="mobile"]'
        ).fill(username)


        await page.locator(
            'input[name="password"]'
        ).fill(password)


        # --------------------------------------------------------
        # CAPTURE CAPTCHA FROM THIS EXACT PAGE
        # --------------------------------------------------------

        await page.locator(
            "#imgCap"
        ).screenshot(
            path=str(CAPTCHA_FILE)
        )


        # Tell caller that captcha is ready.
        write_json(
            STATE_FILE,
            {
                "session_id": session_id,
                "status": "waiting_for_captcha",
                "captcha_file": str(CAPTCHA_FILE),
                "created_at": time.time(),
                "url": page.url,
            },
        )


        # --------------------------------------------------------
        # IMPORTANT:
        #
        # DO NOT close page here.
        # DO NOT reload /login.
        # DO NOT request another captcha.
        #
        # Keep the SAME browser page alive and wait for answer.
        # --------------------------------------------------------

        deadline = (
            time.monotonic()
            + CAPTCHA_TIMEOUT_SECONDS
        )

        captcha_answer = None


        while time.monotonic() < deadline:

            if ANSWER_FILE.exists():

                answer_data = read_json(
                    ANSWER_FILE
                )

                if (
                    answer_data
                    and answer_data.get("session_id")
                    == session_id
                ):

                    captcha_answer = str(
                        answer_data.get(
                            "answer",
                            "",
                        )
                    ).strip()

                    break


            await asyncio.sleep(0.2)


        if not captcha_answer:

            write_json(
                RESULT_FILE,
                {
                    "session_id": session_id,
                    "status": False,
                    "code": "CAPTCHA_TIMEOUT",
                    "error": (
                        "Captcha answer was not received "
                        f"within {CAPTCHA_TIMEOUT_SECONDS} seconds."
                    ),
                },
            )

            return


        # --------------------------------------------------------
        # SUBMIT CAPTCHA ON THE SAME PAGE
        # --------------------------------------------------------

        captcha_input = page.locator(
            'input[name="captcha"]'
        )


        await captcha_input.wait_for(
            state="visible",
            timeout=10000,
        )


        await captcha_input.fill(
            captcha_answer
        )


        # --------------------------------------------------------
        # SUBMIT LOGIN
        # --------------------------------------------------------
        #
        # Based on your previous working FilmazClient, start waiting
        # for navigation BEFORE clicking to avoid race conditions.
        # --------------------------------------------------------

        try:

            async with page.expect_navigation(
                wait_until="domcontentloaded",
                timeout=15000,
            ):

                await page.locator(
                    'button[name="submit"]'
                ).click()


        except PlaywrightTimeoutError:

            # Some login failures may stay on the same URL/page.
            # We inspect the final state below instead of immediately
            # treating this timeout as a fatal Python error.
            pass


        await page.wait_for_timeout(
            1500
        )


        current_url = page.url


        # --------------------------------------------------------
        # VERIFY LOGIN
        # --------------------------------------------------------
        #
        # This comes directly from your previous FilmazClient:
        #
        #     span.DrMenuTxt1
        #
        # is the strongest success indicator.
        # --------------------------------------------------------

        username_text = ""

        try:

            username_locator = page.locator(
                "span.DrMenuTxt1"
            )

            await username_locator.wait_for(
                state="attached",
                timeout=7000,
            )

            username_text = (
                await username_locator.inner_text()
            ).strip()

        except Exception:
            username_text = ""


        if username in username_text:
            
            # Save authenticated cookies/local storage.
            # This includes session cookies that may disappear
            # when Chromium itself closes.
            await context.storage_state(
                path=str(AUTH_STATE_FILE)
            )

            result = {
                "session_id": session_id,
                "status": True,
                "code": "LOGIN_SUCCESS",
                "message": "Login successful.",
                "url": current_url,
                "username_text": username_text,
                "auth_state_file": str(AUTH_STATE_FILE),
            }


        elif "login" in current_url.lower():

            result = {
                "session_id": session_id,
                "status": False,
                "code": "LOGIN_FAILED",
                "error": (
                    "Wrong captcha, wrong credentials, "
                    "or login was rejected."
                ),
                "url": current_url,
            }


        else:

            result = {
                "session_id": session_id,
                "status": False,
                "code": "LOGIN_NOT_VERIFIED",
                "error": (
                    "The page left /login, but the username "
                    "panel span.DrMenuTxt1 was not found."
                ),
                "url": current_url,
                "username_text": username_text,
            }


        # --------------------------------------------------------
        # Close persistent context.
        #
        # Chromium writes authenticated cookies/profile to disk.
        # --------------------------------------------------------

        await context.close()
        context = None


        write_json(
            RESULT_FILE,
            result
        )


        write_json(
            STATE_FILE,
            {
                "session_id": session_id,
                "status": "finished",
                "result": result,
            },
        )


    except Exception as e:

        result = {
            "session_id": session_id,
            "status": False,
            "code": "LOGIN_WORKER_ERROR",
            "error": str(e),
        }

        write_json(
            RESULT_FILE,
            result
        )


        write_json(
            STATE_FILE,
            {
                "session_id": session_id,
                "status": "error",
                "result": result,
            },
        )


    finally:

        try:
            if page and not page.is_closed():
                await page.close()
        except Exception:
            pass


        try:
            if context:
                await context.close()
        except Exception:
            pass


        try:
            if playwright:
                await playwright.stop()
        except Exception:
            pass


        try:
            ANSWER_FILE.unlink()
        except FileNotFoundError:
            pass


        # Remove PID only if it belongs to this worker.
        pid_info = read_json(
            PID_FILE
        )

        if (
            pid_info
            and pid_info.get("pid") == os.getpid()
        ):
            try:
                PID_FILE.unlink()
            except FileNotFoundError:
                pass


# ============================================================
# Launcher
# ============================================================

def start_background_worker():

    existing = current_worker()


    # If an active login worker already exists,
    # simply return the existing captcha.
    if existing:

        state = read_json(
            STATE_FILE
        )

        if (
            state
            and state.get("status")
            == "waiting_for_captcha"
            and CAPTCHA_FILE.exists()
        ):

            print("CAPTCHA_READY")
            print(
                f"CAPTCHA_FILE={CAPTCHA_FILE}"
            )
            print(
                f"LOGIN_ID={state.get('session_id')}"
            )

            return 0


        print("LOGIN_IN_PROGRESS")
        return 0


    # Previous worker is gone.
    cleanup_runtime()
    cleanup_stale_profile_locks()


    session_id = str(
        uuid.uuid4()
    )


    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        session_id,
    ]


    log_handle = open(
        LOG_FILE,
        "ab",
        buffering=0,
    )


    process = subprocess.Popen(
        command,

        stdout=log_handle,
        stderr=log_handle,

        # Important:
        # child continues running after this command returns.
        start_new_session=True,
    )


    write_json(
        PID_FILE,
        {
            "pid": process.pid,
            "session_id": session_id,
            "created_at": time.time(),
        },
    )


    # Wait only until captcha is ready.
    # The browser worker remains alive afterwards.
    deadline = (
        time.monotonic()
        + 30
    )


    while time.monotonic() < deadline:

        state = read_json(
            STATE_FILE
        )


        if (
            state
            and state.get("session_id")
            == session_id
        ):

            if (
                state.get("status")
                == "waiting_for_captcha"
                and CAPTCHA_FILE.exists()
            ):

                print("CAPTCHA_READY")

                print(
                    f"CAPTCHA_FILE={CAPTCHA_FILE}"
                )

                print(
                    f"LOGIN_ID={session_id}"
                )

                return 0


            if state.get("status") == "error":

                print("LOGIN_START_FAILED")

                result = state.get(
                    "result",
                    {},
                )

                print(
                    f"ERROR={result.get('error', 'unknown')}"
                )

                print(
                    f"LOG_FILE={LOG_FILE}"
                )

                return 1


        if process.poll() is not None:

            result = read_json(
                RESULT_FILE
            )

            print("LOGIN_START_FAILED")

            if result:
                print(
                    f"ERROR={result.get('error', 'unknown')}"
                )

            print(
                f"LOG_FILE={LOG_FILE}"
            )

            return 1


        time.sleep(0.2)


    # Worker took too long. Stop it to avoid leaving
    # a hidden Chromium process running forever.
    try:
        os.killpg(
            process.pid,
            signal.SIGTERM,
        )
    except Exception:
        pass


    print("LOGIN_START_TIMEOUT")
    print(
        f"LOG_FILE={LOG_FILE}"
    )

    return 1


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    if (
        len(sys.argv) >= 3
        and sys.argv[1] == "--worker"
    ):

        asyncio.run(
            login_worker(
                sys.argv[2]
            )
        )

    else:

        sys.exit(
            start_background_worker()
        )