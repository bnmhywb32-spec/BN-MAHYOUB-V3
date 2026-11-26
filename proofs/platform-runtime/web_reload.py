"""Run the built Flutter application twice on the same Chromium storage origin."""

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from playwright.sync_api import sync_playwright


def run(build_path):
    handler = partial(SimpleHTTPRequestHandler, directory=build_path)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome")
                page = browser.new_page()
                url = f"http://127.0.0.1:{server.server_port}"

                def verify(action, expected):
                    with page.expect_console_message(
                        predicate=lambda message: "BN_V3_PROOF:" in message.text,
                        timeout=60_000,
                    ) as output:
                        action()
                    response = output.value.text
                    if not response.endswith(expected):
                        raise AssertionError(response)
                    print(response)

                verify(lambda: page.goto(url), "PASS: durable sale saved; relaunch to verify persistence")
                verify(page.reload, "PASS: persisted sale verified after relaunch")
                browser.close()
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    run(sys.argv[1])
