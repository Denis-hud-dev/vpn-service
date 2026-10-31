"""The real deployment probe must work without importing the app from cwd."""
import ast
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading

import pytest

INSTALLER=Path(__file__).resolve().parents[1]/'deploy/website-review/enable_payments.py'


@pytest.mark.parametrize('status',[200,503])
def test_stdlib_probe_from_empty_cwd_checks_actual_http_readiness(tmp_path,status):
    tree=ast.parse(INSTALLER.read_text())
    probes=[node.value.value for node in ast.walk(tree) if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id=='probe' for target in node.targets)
            and isinstance(node.value,ast.Constant)]
    assert len(probes)==1
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            body=json.dumps({'ready':True,'test':True}).encode()
            self.send_response(status);self.send_header('Content-Length',str(len(body)));self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):pass
    # Keep Unix address short enough for Darwin as well as Linux.
    with tempfile.TemporaryDirectory(prefix='kuda-probe-',dir='/tmp') as directory:
        path=directory+'/rpc.sock'
        with socketserver.UnixStreamServer(path,Handler) as server:
            server.timeout=8
            thread=threading.Thread(target=server.handle_request,daemon=True)
            thread.start()
            probe=probes[0].replace('/run/kudavpn-site-payments/bridge.sock',path)
            result=subprocess.run([sys.executable,'-I','-c',probe],cwd=tmp_path,capture_output=True,text=True,timeout=10)
            thread.join(timeout=10)
            assert not thread.is_alive()
            assert (result.returncode==0)==(status==200)
            assert seen==[('/rpc',{'action':'ready'})]
