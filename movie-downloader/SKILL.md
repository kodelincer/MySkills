# Movie Downloader

Use the provided scripts. Do not manually automate the website unless a script reports an error.

## Login

Run:

scripts/flzios_login_start.py

When CAPTCHA_READY is returned, present the CAPTCHA_FILE image to the user.

Wait for the answer, then run:

scripts/flzios_login_submit.py <answer>

Continue only after LOGIN_SUCCESS.


## Search

Run:

scripts/flzios_movie.py search "<title>"

Show the numbered results and ask the user to choose a number.


## Movie selection

Run:

scripts/flzios_movie.py qualities <movie_number>

Show the numbered qualities and ask the user to choose a number.


## Download

Run:

scripts/flzios_movie.py download <quality_number>

Report the movie, quality, size and GID.


## Progress

When the user asks for progress, run:

scripts/flzios_movie.py status

Do not use Playwright or browser automation while the file is downloading.
aria2 handles the download.

## Authentication state

The login scripts save authenticated browser state automatically.

Do not inspect, modify, or delete runtime files after LOGIN_SUCCESS.

After LOGIN_SUCCESS, immediately run the requested search.

If flzios_movie.py returns NOT_LOGGED_IN, perform the login flow again.

Do not troubleshoot authentication unless a script returns an explicit error.