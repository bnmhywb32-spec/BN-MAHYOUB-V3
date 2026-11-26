"""Run the built platform proof twice on the same Chromium storage origin."""

import argparse
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from playwright.sync_api import sync_playwright


SEEDED = "PASS: durable sale saved; relaunch to verify persistence"
REOPENED = "PASS: persisted sale verified after relaunch"


def proof_message(message):
    return "BN_V3_PROOF:" in message.text


def verify_tabs(browser, url, lock_name):
    context = browser.new_context()
    try:
        control = context.new_page()
        control.route("**/proof-lock-control", lambda route: route.fulfill(body="<html></html>", content_type="text/html"))
        control.goto(url + "/proof-lock-control")
        control.evaluate("""
            name => new Promise(resolve => {
                navigator.locks.request(name, () => new Promise(release => {
                    window.releaseProofLock = release;
                    resolve();
                }));
            })
        """, lock_name)
        first = context.new_page()
        second = context.new_page()
        with first.expect_console_message(predicate=proof_message, timeout=60_000) as left:
            with second.expect_console_message(predicate=proof_message, timeout=60_000) as right:
                first.goto(url, wait_until="commit")
                second.goto(url, wait_until="commit")
                control.wait_for_function("""
                    async name => (await navigator.locks.query()).pending
                        .filter(lock => lock.name === name).length === 2
                """, arg=lock_name, timeout=45_000)
                control.evaluate("window.releaseProofLock()")
        messages = {entry.value.text.split("BN_V3_PROOF: ", 1)[-1] for entry in (left, right)}
        if messages != {SEEDED, REOPENED}:
            raise AssertionError(messages)
        print("PASS: two Chromium tabs queued on the storage lock and shared one sale")
    finally:
        context.close()


def run(build_path, lock_name, test_abort):
    handler = partial(SimpleHTTPRequestHandler, directory=build_path)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as playwright:
                executable = os.getenv("BN_PROOF_CHROMIUM")
                browser = playwright.chromium.launch(
                    **({"executable_path": executable} if executable else {"channel": "chrome"})
                )
                page = browser.new_page()
                url = f"http://127.0.0.1:{server.server_port}"

                def verify(action, expected):
                    with page.expect_console_message(
                        predicate=proof_message,
                        timeout=60_000,
                    ) as output:
                        action()
                    response = output.value.text
                    if not response.endswith(expected):
                        raise AssertionError(response)
                    print(response)

                if test_abort:
                    verify(lambda: page.goto(url + "/?abort-write"), "FAIL: Injected snapshot abort")
                verify(lambda: page.goto(url), SEEDED)
                verify(page.reload, REOPENED)
                if test_abort:
                    verify(lambda: page.goto(url + "/?abort-write"), "FAIL: Injected snapshot abort")
                    verify(lambda: page.goto(url), REOPENED)
                if lock_name:
                    verify_tabs(browser, url, lock_name)
                browser.close()
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("build_path")
    parser.add_argument("--lock-name")
    parser.add_argument("--test-abort", action="store_true")
    args = parser.parse_args()
    run(args.build_path, args.lock_name, args.test_abort)
