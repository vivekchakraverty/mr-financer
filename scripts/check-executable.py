"""Verify a packaged executable from an empty directory on its target OS."""
import io
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from zipfile import ZipFile

import httpx
from PIL import Image


BROWSER_CHECK = """
const { chromium } = require(process.argv[1]);
const url = process.argv[2];
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({serviceWorkers: "block"});
    await page.route("**/*", route => route.request().url().startsWith(url)
      ? route.continue() : route.abort());
    const response = await page.goto(url);
    if (!response.ok()) throw new Error("Could not open the packaged workspace");
    await page.locator('a[data-page-link="documents"]').click();
    await page.locator("#plan-form [data-field=idea]").waitFor({state: "visible"});
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def check_routes(client, version):
    home = client.get("/").raise_for_status()
    assert 'id="business-plan"' in home.text
    assert client.get("/openapi.json").raise_for_status().json()["info"]["version"] == version
    assert client.get("/assets/downloads.js").raise_for_status().content
    assert client.get("/incorporation/countries").raise_for_status().json()
    assert client.get("/business-plan/pitch/questions").raise_for_status().json()["groups"]

    plan = {"blocks": [{"kind": "paragraph", "text": "This plan tests the packaged export."}]}
    pdf = client.post("/business-plan/export/pdf", json=plan).raise_for_status()
    assert pdf.content.startswith(b"%PDF-")
    word = client.post("/business-plan/export/docx", json=plan).raise_for_status()
    with ZipFile(io.BytesIO(word.content)) as file:
        assert b"packaged export" in file.read("word/document.xml")

    picture = io.BytesIO()
    with Image.new("RGB", (160, 90), "green") as image:
        image.save(picture, "PNG")
    deck = client.post("/business-plan/pitch/export/pptx", files={
        "images": ("slide.png", picture.getvalue(), "image/png"),
    }).raise_for_status()
    with ZipFile(io.BytesIO(deck.content)) as file:
        assert "ppt/slides/slide1.xml" in file.namelist()


def check_browser(unpack, url, cwd, env):
    driver, = unpack.glob("_MEI*/playwright/driver")
    node = driver / ("node.exe" if os.name == "nt" else "node")
    subprocess.run(
        [str(node), "-e", BROWSER_CHECK, str(driver / "package"), url],
        env={**env, "PLAYWRIGHT_BROWSERS_PATH": "0"}, cwd=cwd, check=True, timeout=45,
    )


def wait_until_ready(client, process):
    deadline = time.monotonic() + 90
    while process.poll() is None and time.monotonic() < deadline:
        try:
            if client.get("/health", timeout=1).is_success:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.25)
    raise RuntimeError("The executable did not start the local workspace.")


def stop(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, check=False, timeout=15)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=15)


def main():
    executable, version = Path(sys.argv[1]).resolve(), sys.argv[2]
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp)
        unpack = cwd / "runtime"
        unpack.mkdir()
        env = {**os.environ, "TEMP": str(unpack), "TMP": str(unpack), "TMPDIR": str(unpack)}
        result = subprocess.check_output([str(executable), "--version"], cwd=cwd, env=env, timeout=90)
        assert version in result.decode()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        url = f"http://127.0.0.1:{port}/"
        log = cwd / "workspace.log"
        with log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                [str(executable), "--no-browser", "--port", str(port)], cwd=cwd,
                env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
            )
            try:
                with httpx.Client(base_url=url, trust_env=False, timeout=8) as client:
                    wait_until_ready(client, process)
                    check_routes(client, version)
                check_browser(unpack, url, cwd, env)
            finally:
                stop(process)
                print(log.read_text(encoding="utf-8", errors="replace"))
    print(f"Verified {executable.name}, its browser, and PDF, DOCX, and PPTX exports.")


if __name__ == "__main__":
    main()
