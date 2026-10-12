"""Secure web requests that work on any Python, including python.org's macOS build before
"Install Certificates.command" has been run (it ships with no CA certificates, so every plain
urllib HTTPS request fails with CERTIFICATE_VERIFY_FAILED).

    from ai_editor import net
    with net.urlopen(req, 20) as r: ...
    net.friendly(err)          # a plain sentence for a certificate error, else the error text

Certificates are always verified. context() only adds trusted roots: SSL_CERT_FILE / SSL_CERT_DIR
and the system store (what ssl.create_default_context() loads), plus certifi's bundle when it is
importable, else the editor venv's certifi bundle found by path (the window's server runs under the
system python3), plus the first OS bundle file that loads when nothing else loaded any.
creator-teardown keeps a copy of context() in scripts/_net.py; check_plugins.py keeps them identical.

  python3 net.py demo     proves context() has CA certs where create_default_context() has none
"""
import ssl
import sys
import urllib.request

FIX = ("This computer's Python can't check secure websites yet. Say 'fix my certificates' in Claude Code "
       "(or run Install Certificates.command in your Python folder).")


def context():
    """An SSLContext that verifies certificates and has CA certs on every Python we know of.
    Built per call (about 20 ms), so a fix made while the window runs works on the next request."""
    import glob
    import os
    ctx = ssl.create_default_context()   # honours SSL_CERT_FILE / SSL_CERT_DIR, else the system store
    try:
        import certifi
        bundles = [certifi.where()]
    except ImportError:   # system python3: the editor venv's certifi, by path, without importing it
        home = os.environ.get("AI_EDITOR_HOME") or os.path.join(os.path.expanduser("~"), ".ai-video-editor")
        sp = [os.path.join(home, "venv", "lib", "python*", "site-packages"),
              os.path.join(home, "venv", "Lib", "site-packages")]
        bundles = sorted(f for d in sp for f in glob.glob(os.path.join(d, "certifi", "cacert.pem")))[-1:]
    system = ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]
    for p in bundles + system:
        if ctx.cert_store_stats()["x509_ca"] and p not in bundles:
            break   # an OS bundle only when nothing loaded any
        if os.path.isfile(p):
            try:
                ctx.load_verify_locations(p)
            except (OSError, ssl.SSLError):
                pass
    return ctx


def urlopen(req, timeout=60):
    """urllib.request.urlopen with context(). Looked up at call time, so tests can stub urlopen."""
    return urllib.request.urlopen(req, timeout=timeout, context=context())


def retrieve(url, dest, timeout=60, reporthook=None):
    """urlretrieve with context(): url's bytes into dest. reporthook(blocks, block_size, total) like urlretrieve."""
    bs = 1 << 16
    with urlopen(url, timeout) as r, open(dest, "wb") as f:
        total, n = int(r.headers.get("Content-Length") or -1), 0
        if reporthook:
            reporthook(0, bs, total)
        while True:
            chunk = r.read(bs)
            if not chunk:
                break
            f.write(chunk)
            n += 1
            if reporthook:
                reporthook(n, bs, total)


def is_cert_error(err):
    reason = getattr(err, "reason", err)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(err)


def friendly(err):
    """FIX for a certificate error, else the error's own text (a URLError's reason)."""
    return FIX if is_cert_error(err) else str(getattr(err, "reason", err))


BLOCK_CERTIFI = ("import sys\n"
                 "class _NoCertifi:\n"
                 "    def find_spec(self, name, path=None, target=None):\n"
                 "        if name == 'certifi': raise ImportError('certifi blocked')\n"
                 "sys.meta_path.insert(0, _NoCertifi())\n")


def demo():
    """Python with no CA certs (what python.org's macOS build is before Install Certificates): SSL_CERT_FILE an
    empty file, SSL_CERT_DIR an empty folder, run in a subprocess. With certifi, then with certifi blocked (the
    window's server under system python3): first falling back to the OS bundle, then to the venv's by path."""
    import os
    import subprocess
    import tempfile
    import urllib.error
    assert context().verify_mode == ssl.CERT_REQUIRED and context().check_hostname
    lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Windows also loads its own store, and Apple's LibreSSL Python its own, which these variables do not touch
    honours_env = sys.platform != "win32" and ssl.OPENSSL_VERSION.startswith("OpenSSL")
    with tempfile.TemporaryDirectory() as d:
        empty = os.path.join(d, "empty.pem")
        open(empty, "w", encoding="utf-8").close()
        os.mkdir(os.path.join(d, "certs"))
        os.mkdir(os.path.join(d, "nohome"))
        venv_home = os.path.join(d, "home")
        sp = os.path.join(venv_home, "venv", *(["Lib"] if os.name == "nt" else ["lib", "python3.99"]), "site-packages")
        os.makedirs(os.path.join(sp, "certifi"))
        one = ssl.DER_cert_to_PEM_cert(context().get_ca_certs(binary_form=True)[0])
        with open(os.path.join(sp, "certifi", "cacert.pem"), "w", encoding="utf-8") as f:
            f.write(one)
        code = ("import ssl, sys; sys.path.insert(0, sys.argv[1]); from ai_editor import net; "
                "print(ssl.create_default_context().cert_store_stats()['x509_ca'], "
                "net.context().cert_store_stats()['x509_ca'])")

        def run(home, block):
            env = {**os.environ, "SSL_CERT_FILE": empty, "SSL_CERT_DIR": os.path.join(d, "certs"),
                   "AI_EDITOR_HOME": home}
            r = subprocess.run([sys.executable, "-I", "-c", (BLOCK_CERTIFI if block else "") + code, lib], env=env,
                               capture_output=True, text=True, encoding="utf-8")
            assert r.returncode == 0, r.stderr
            return tuple(map(int, r.stdout.split()))
        for label, home, block in (("certifi if installed", os.path.join(d, "nohome"), False),
                                   ("no certifi, OS bundle", os.path.join(d, "nohome"), True),
                                   ("no certifi, venv bundle by path", venv_home, True)):
            bare, ours = run(home, block)
            print(f"broken env, {label}: create_default_context() {bare} CA certs, net.context() {ours}")
            assert ours > 0, f"{label}: net.context() found no CA certs"
            if honours_env:
                assert bare == 0, f"create_default_context() should have no CA certs here, has {bare}"
                if home == venv_home:
                    assert ours == 1, f"the venv's certifi bundle was not loaded first ({ours})"
    e = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate "
                                                              "verify failed: unable to get local issuer certificate"))
    assert friendly(e) == FIX and "SSL" not in friendly(e) and "urlopen" not in friendly(e)
    assert friendly(urllib.error.URLError("nodename nor servname provided")) == "nodename nor servname provided"
    print("demo ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["demo"]:
        demo()
    else:
        sys.exit(__doc__)
