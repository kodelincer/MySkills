#!/usr/bin/env python3

import os
import sys
import json
import asyncio
import argparse
import urllib.parse
from pathlib import Path

from dotenv import load_dotenv

from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

import aria2_rpc


# ============================================================
# PATHS
# ============================================================

HERMES_DIR = (
    Path.home()
    / ".hermes"
)


ENV_FILE = (
    HERMES_DIR
    / "secrets"
    / "movie-site.env"
)


RUNTIME_DIR = (
    HERMES_DIR
    / "runtime"
    / "movie-downloader"
)


PROFILE_DIR = (
    HERMES_DIR
    / "profiles"
    / "flzios"
)


SEARCH_FILE = (
    RUNTIME_DIR
    / "search_results.json"
)


QUALITY_FILE = (
    RUNTIME_DIR
    / "quality_results.json"
)


LAST_DOWNLOAD_FILE = (
    RUNTIME_DIR
    / "last_download.json"
)

AUTH_STATE_FILE = (
    HERMES_DIR
    / "runtime"
    / "movie-downloader"
    / "flzios_auth_state.json"
)

RUNTIME_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


PROFILE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# ENV
# ============================================================

load_dotenv(
    ENV_FILE
)


SITE_URL = (
    os.getenv("SITE_URL")
    or os.getenv("site_url")
    or "https://flzios.com"
)


# ============================================================
# JSON HELPERS
# ============================================================

def save_json(
    path,
    data,
):

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )


    temp.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


    os.replace(
        temp,
        path,
    )


def load_json(
    path,
):

    try:

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return None


# ============================================================
# PLAYWRIGHT
# ============================================================

async def open_browser_context(playwright):

    if not AUTH_STATE_FILE.exists():

        raise RuntimeError(
            "AUTH_STATE_NOT_FOUND"
        )

    browser = await playwright.chromium.launch(
        headless=True
    )

    context = await browser.new_context(
        storage_state=str(AUTH_STATE_FILE),

        viewport={
            "width": 1366,
            "height": 768,
        },

        ignore_https_errors=True,
    )

    return browser, context


async def get_text(
    locator,
):

    try:

        return (
            await locator.inner_text()
        ).strip()

    except Exception:

        return ""


async def check_login(page):

    # Obvious redirect to login page
    if "login" in page.url.lower():

        print("NOT_LOGGED_IN")
        print("Run login first.")

        return False


    # The site uses this element after successful login.
    try:

        user_panel = page.locator(
            "span.DrMenuTxt1"
        )

        await user_panel.wait_for(
            state="attached",
            timeout=3000,
        )

        return True

    except Exception:

        # Some pages may not display the user panel,
        # so URL is our fallback.
        if "login" not in page.url.lower():

            return True


    print("NOT_LOGGED_IN")

    return False


# ============================================================
# SEARCH MOVIE
# ============================================================

async def search_movies(query):

    query = query.strip()

    if not query:

        print("EMPTY_SEARCH")
        return 1


    async with async_playwright() as p:

        try:

            browser, context = await open_browser_context(p)

        except RuntimeError as e:

            if str(e) == "AUTH_STATE_NOT_FOUND":

                print("NOT_LOGGED_IN")
                print("Run login first.")

                return 1

            raise


        page = await context.new_page()

        try:

            # --------------------------------------------------
            # 1. OPEN REAL SEARCH PAGE
            # --------------------------------------------------

            search_page_url = (
                f"{SITE_URL.rstrip('/')}/search"
            )

            await page.goto(
                search_page_url,
                wait_until="domcontentloaded",
                timeout=30000,
            )


            if not await check_login(page):
                return 1


            # --------------------------------------------------
            # 2. WAIT FOR REAL SEARCH FORM
            # --------------------------------------------------

            search_form = page.locator(
                'form[method="GET"]'
            ).first


            search_input = page.locator(
                'input[name="q"]'
            ).first


            submit_button = page.locator(
                'input[type="submit"][name="submit"]'
            ).first


            try:

                await search_form.wait_for(
                    state="attached",
                    timeout=10000,
                )


                await search_input.wait_for(
                    state="visible",
                    timeout=10000,
                )


                await submit_button.wait_for(
                    state="visible",
                    timeout=10000,
                )


            except PlaywrightTimeoutError:

                print(
                    "SEARCH_FORM_NOT_FOUND"
                )

                return 1


            # --------------------------------------------------
            # IMPORTANT:
            #
            # Website has:
            #
            # target="_blank"
            #
            # which normally opens a new tab.
            #
            # Remove it so our headless script can continue
            # in the SAME page.
            # --------------------------------------------------

            await search_form.evaluate(
                """
                form => {
                    form.removeAttribute('target');
                }
                """
            )


            # --------------------------------------------------
            # 3. ENTER MOVIE TITLE
            # --------------------------------------------------

            await search_input.fill(
                query
            )


            # --------------------------------------------------
            # 4. SUBMIT REAL WEBSITE FORM
            # --------------------------------------------------

            try:

                async with page.expect_navigation(
                    wait_until="domcontentloaded",
                    timeout=30000,
                ):

                    await submit_button.click()


            except PlaywrightTimeoutError:

                # Sometimes page finishes very fast and
                # Playwright may miss navigation.
                #
                # If URL contains q= we can safely continue.
                if "q=" not in page.url:

                    print(
                        "SEARCH_NAVIGATION_FAILED"
                    )

                    print(
                        f"URL={page.url}"
                    )

                    return 1


            # --------------------------------------------------
            # 5. WAIT FOR RESULT CONTAINER
            # --------------------------------------------------

            try:

                await page.locator(
                    ".movie_list"
                ).wait_for(
                    state="attached",
                    timeout=15000,
                )


            except PlaywrightTimeoutError:

                print(
                    "SEARCH_RESULTS_CONTAINER_NOT_FOUND"
                )

                print(
                    f"URL={page.url}"
                )

                return 1


            # Small delay for any final rendering
            await page.wait_for_timeout(
                300
            )


            # --------------------------------------------------
            # 6. READ MOVIES
            # --------------------------------------------------

            movie_items = page.locator(
                ".movie_list .movie_item"
            )


            count = await movie_items.count()


            results = []


            for i in range(count):

                item = movie_items.nth(i)


                link_element = item.locator(
                    "a"
                ).first


                href = await link_element.get_attribute(
                    "href"
                )


                if not href:
                    continue


                # Example:
                #
                # movie?m=3547
                #
                # becomes:
                #
                # https://flzios.com/movie?m=3547
                #
                movie_url = urllib.parse.urljoin(
                    page.url,
                    href,
                )


                title = await get_text(
                    item.locator(
                        ".movie_item_title"
                    )
                )


                year = await get_text(
                    item.locator(
                        ".movie_item_year"
                    )
                )


                imdb = await get_text(
                    item.locator(
                        ".movie_item_imdb"
                    )
                )


                results.append(
                    {
                        "index":
                            len(results) + 1,

                        "title":
                            title,

                        "year":
                            year,

                        "imdb":
                            imdb,

                        "url":
                            movie_url,
                    }
                )


            # --------------------------------------------------
            # 7. SAVE RESULT FOR NEXT USER SELECTION
            # --------------------------------------------------

            save_json(
                SEARCH_FILE,
                {
                    "query":
                        query,

                    "search_url":
                        page.url,

                    "results":
                        results,
                },
            )


            # --------------------------------------------------
            # 8. OUTPUT FOR HERMES
            # --------------------------------------------------

            print(
                f"SEARCH_OK COUNT={len(results)}"
            )


            if not results:

                print(
                    "NO_MOVIES_FOUND"
                )

                return 0


            for movie in results:

                print(
                    f"{movie['index']}. "
                    f"{movie['title']} | "
                    f"Year: {movie['year'] or '-'} | "
                    f"IMDb: {movie['imdb'] or '-'}"
                )


            return 0


        except Exception as e:

            print(
                "SEARCH_FAILED"
            )

            print(
                f"ERROR={e}"
            )

            print(
                f"URL={page.url}"
            )

            return 1


        finally:

            try:

                await page.close()

            except Exception:

                pass


            await context.close()
            await browser.close()

# ============================================================
# LIST QUALITIES
# ============================================================

async def list_qualities(
    movie_index,
):

    search_data = load_json(
        SEARCH_FILE
    )


    if not search_data:

        print(
            "NO_SEARCH_RESULTS"
        )

        return 1


    movies = search_data.get(
        "results",
        [],
    )


    if (
        movie_index < 1
        or movie_index > len(movies)
    ):

        print(
            "INVALID_MOVIE_NUMBER"
        )

        return 1


    movie = movies[
        movie_index - 1
    ]


    movie_url = movie[
        "url"
    ]


    async with async_playwright() as p:


        try:

            browser, context = await open_browser_context(p)

        except RuntimeError as e:

            if str(e) == "AUTH_STATE_NOT_FOUND":

                print("NOT_LOGGED_IN")
                print("Run login first.")

                return 1

            raise


        page = await context.new_page()


        try:

            await page.goto(
                movie_url,
                wait_until="domcontentloaded",
                timeout=30000,
            )


            if not await check_login(
                page
            ):

                return 1


            quality_links = page.locator(
                "#result2 a"
            )


            try:

                await quality_links.first.wait_for(
                    state="attached",
                    timeout=15000,
                )

            except PlaywrightTimeoutError:

                print(
                    "NO_QUALITY_LINKS"
                )

                return 1


            await page.wait_for_timeout(
                500
            )


            count = await quality_links.count()


            user_agent = await page.evaluate(
                "() => navigator.userAgent"
            )


            qualities = []


            for i in range(count):


                link = quality_links.nth(i)


                href = await link.get_attribute(
                    "href"
                )


                if not href:

                    continue


                download_url = urllib.parse.urljoin(
                    page.url,
                    href,
                )


                quality = await get_text(
                    link.locator(
                        ".w70"
                    )
                )


                size = await get_text(
                    link.locator(
                        ".w30"
                    )
                )


                # ==============================================
                # Build headers for aria2.
                #
                # Important:
                # these cookies come from the authenticated
                # Playwright profile.
                # ==============================================

                cookies = await context.cookies(
                    [download_url]
                )


                cookie_header = "; ".join(

                    f"{cookie['name']}="
                    f"{cookie['value']}"

                    for cookie in cookies
                )


                headers = [

                    f"Referer: {page.url}",

                    f"User-Agent: {user_agent}",
                ]


                if cookie_header:

                    headers.append(
                        f"Cookie: {cookie_header}"
                    )


                qualities.append(
                    {
                        "index":
                            len(qualities) + 1,

                        "quality":
                            quality or "Unknown",

                        "size":
                            size or "-",

                        "url":
                            download_url,

                        "headers":
                            headers,
                    }
                )


            save_json(
                QUALITY_FILE,

                {
                    "movie":
                        movie,

                    "qualities":
                        qualities,
                },
            )


            print(
                f"QUALITIES_OK COUNT={len(qualities)}"
            )


            print(
                f"MOVIE={movie['title']}"
            )


            for quality in qualities:


                print(
                    f"{quality['index']}. "
                    f"{quality['quality']} | "
                    f"Size: {quality['size']}"
                )


            return 0


        finally:

            try:

                await page.close()

            except Exception:

                pass


            await context.close()
            await browser.close()


# ============================================================
# START DOWNLOAD USING YOUR aria2_rpc.py
# ============================================================

def download(
    quality_index,
):

    data = load_json(
        QUALITY_FILE
    )


    if not data:

        print(
            "NO_QUALITY_RESULTS"
        )

        return 1


    qualities = data.get(
        "qualities",
        [],
    )


    if (
        quality_index < 1
        or quality_index > len(qualities)
    ):

        print(
            "INVALID_QUALITY_NUMBER"
        )

        return 1


    selected = qualities[
        quality_index - 1
    ]


    movie = data.get(
        "movie",
        {},
    )


    try:

        gid = aria2_rpc.add(

            url=selected["url"],

            headers=selected.get(
                "headers",
                [],
            ),
        )


    except Exception as e:

        print(
            "DOWNLOAD_FAILED"
        )

        print(
            f"ERROR={e}"
        )

        return 1


    download_data = {

        "gid":
            gid,

        "movie":
            movie.get(
                "title",
                "",
            ),

        "quality":
            selected.get(
                "quality",
                "",
            ),

        "size":
            selected.get(
                "size",
                "",
            ),

        "url":
            selected.get(
                "url",
                "",
            ),
    }


    save_json(
        LAST_DOWNLOAD_FILE,
        download_data,
    )


    print(
        "DOWNLOAD_STARTED"
    )


    print(
        f"GID={gid}"
    )


    print(
        f"MOVIE={download_data['movie']}"
    )


    print(
        f"QUALITY={download_data['quality']}"
    )


    print(
        f"SIZE={download_data['size']}"
    )


    return 0


# ============================================================
# DOWNLOAD STATUS
# ============================================================

def download_status(
    gid=None,
):

    if not gid:

        last = load_json(
            LAST_DOWNLOAD_FILE
        )


        if not last:

            print(
                "NO_LAST_DOWNLOAD"
            )

            return 1


        gid = last.get(
            "gid"
        )


    try:

        info = aria2_rpc.get_status(
            gid
        )


    except Exception as e:

        print(
            "STATUS_FAILED"
        )

        print(
            f"ERROR={e}"
        )

        return 1


    print(
        "DOWNLOAD_STATUS"
    )


    print(
        f"GID={gid}"
    )


    print(
        f"STATUS={info['status']}"
    )


    print(
        f"PERCENT={info['percent']}"
    )


    print(
        f"SPEED={info['speed_bytes_sec']}"
    )


    print(
        f"ETA={info['eta_seconds']}"
    )


    return 0


# ============================================================
# CLI
# ============================================================

def parser():

    p = argparse.ArgumentParser(
        description="Flzios movie helper"
    )


    sub = p.add_subparsers(
        dest="command",
        required=True,
    )


    search = sub.add_parser(
        "search"
    )

    search.add_argument(
        "query"
    )


    qualities = sub.add_parser(
        "qualities"
    )

    qualities.add_argument(
        "movie_number",
        type=int,
    )


    download_parser = sub.add_parser(
        "download"
    )

    download_parser.add_argument(
        "quality_number",
        type=int,
    )


    status_parser = sub.add_parser(
        "status"
    )

    status_parser.add_argument(
        "gid",
        nargs="?",
        default=None,
    )


    return p


def main():

    args = parser().parse_args()


    if args.command == "search":

        return asyncio.run(
            search_movies(
                args.query
            )
        )


    if args.command == "qualities":

        return asyncio.run(
            list_qualities(
                args.movie_number
            )
        )


    if args.command == "download":

        return download(
            args.quality_number
        )


    if args.command == "status":

        return download_status(
            args.gid
        )


    return 1


if __name__ == "__main__":

    sys.exit(
        main()
    )