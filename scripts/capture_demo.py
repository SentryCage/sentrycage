"""Capture genuine demo stdout as a local HTML view; no hand-written alerts.

Run with the project Python, then screenshot the resulting HTML with a browser.
This developer script is not included in the runtime image.
"""
import html
import json
import time
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
assets = ROOT / 'docs/assets'
with tempfile.TemporaryDirectory(prefix='sentrycage-capture-') as directory:
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, '-u', str(ROOT/'main.py'), '--demo'],
                               cwd=directory, env={'PATH': os.environ['PATH'], 'PYTHONUTF8': '1'},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    frames = []
    output = ''
    for line in process.stdout:
        output += line
        frames.append([round(time.monotonic() - started, 6), 'o', line.replace('\n', '\r\n')])
    stderr = process.stderr.read()
    assert process.wait(timeout=20) == 0, stderr
recording = [json.dumps({'version': 2, 'width': 160, 'height': 30,
                         'title': 'SentryCage DEMO — synthetic data, actual stdout',
                         'env': {'TERM': 'xterm-256color'}})]
recording.extend(json.dumps(frame, ensure_ascii=False) for frame in frames)
(assets/'demo.cast').write_text('\n'.join(recording) + '\n')
assert 'DEMO — dati sintetici' in output
assert 'SIGNAL: accumulation' in output
(assets/'demo-output.txt').write_text(output)
view = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>SentryCage actual demo output</title>
<style>body{background:#101723;color:#dce7f5;margin:0;padding:32px;font-family:system-ui}
h1{font-size:28px;color:#73e4c3;margin:0 0 12px}p{font-size:16px;color:#a8b7ce}
pre{padding:22px;border:1px solid #33445e;border-radius:10px;background:#07101b;
font:16px/1.6 monospace;white-space:pre-wrap;overflow-wrap:anywhere}
</style><h1>SentryCage — DEMO — synthetic data</h1>
<p>Captured from the real program: python main.py --demo · console output, no Telegram delivery</p>
<pre>'''+html.escape(output)+'''</pre><p>Try the offline demo using the README command.</p></html>'''
(assets/'demo-output.html').write_text(view)
print('Captured actual stdout and local view:', assets)
