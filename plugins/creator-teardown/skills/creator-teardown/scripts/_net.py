"""Secure web requests for creator-teardown: a copy of ai-editor's lib/ai_editor/net.py context()
(check_plugins.py keeps the two identical), so python.org's macOS Python works before
"Install Certificates.command" has been run. Certificates are always verified."""
import ssl
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
    return urllib.request.urlopen(req, timeout=timeout, context=context())


def friendly(err):
    """FIX for a certificate error, else the error's own text (a URLError's reason)."""
    reason = getattr(err, "reason", err)
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(err):
        return FIX
    return str(reason)
