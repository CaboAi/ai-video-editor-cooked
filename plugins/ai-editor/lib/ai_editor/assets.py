#!/usr/bin/env python3
"""The images and links a user adds to one video: New video's "Add images or links" and the editor's
"Anything to add?". Stdlib only, stored on this computer only.

    <edit>/assets/asset-img1.png ...   the images (png, jpeg, gif, webp; under 15 MB each, 12 at most)
    <edit>/assets.json                 {"items": [{"kind": "image", "src": "assets/asset-img1.png", "name": "chart.png", "say": ""},
                                                  {"kind": "link", "url": "https://x.com/...", "site": "x", "say": "the launch"}]}

`say` is the optional "Show when I say..." words. `site` is x, youtube, github or web. visual-prep, style-edit and
overlays put every item in first, as a card on its `say` words (else the line it is about), before searching for
anything else (images.json for an image; a post, youtube, github or capture beat in visuals.json for a link).

    python3 assets.py show <edit>      print assets.json
    python3 assets.py demo             self-check
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import askhook  # noqa: E402

MAX_LINKS = 20
SITES = {"x.com": "x", "twitter.com": "x", "youtube.com": "youtube", "youtu.be": "youtube", "github.com": "github"}


def site(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    host = re.sub(r"^(www\.|m\.|mobile\.)", "", host)
    return SITES.get(host, "web")


def load(edit):
    try:
        return json.loads((Path(edit) / "assets.json").read_text(encoding="utf-8")).get("items") or []
    except (OSError, ValueError, AttributeError):
        return []


def _save(edit, items):
    f = Path(edit) / "assets.json"
    if not items:
        f.unlink(missing_ok=True)
        return items
    f.write_text(json.dumps({"items": items}, indent=1) + "\n", encoding="utf-8")
    return items


def add(edit, new):
    """new: [{"url": ..., "say": ...} | {"image": "data:image/...", "name": ..., "say": ...}]. A link that is not
    http(s), a repeat, or one over the limits is dropped. The full list after."""
    edit, items = Path(edit), load(edit)
    for it in new or []:
        if not isinstance(it, dict):
            continue
        say = re.sub(r"\s+", " ", str(it.get("say") or "")).strip()[:120]
        url = str(it.get("url") or "").strip()
        if url:
            if not re.match(r"https?://[^\s/]+\.[^\s]+$", url, re.I) or len(url) > 2000 \
                    or any(x.get("url") == url for x in items) or sum(x["kind"] == "link" for x in items) >= MAX_LINKS:
                continue
            items.append({"kind": "link", "url": url, "site": site(url), "say": say})
        elif it.get("image"):
            have = sum(x["kind"] == "image" for x in items)
            if have >= askhook.MAX_IMAGES:
                continue
            saved = askhook.save_images(edit / "assets", [it["image"]], "asset")
            if saved:
                name = Path(str(it.get("name") or saved[0])).name[:120]
                items.append({"kind": "image", "src": f"assets/{saved[0]}", "name": name, "say": say})
    return _save(edit, items)


def remove(edit, i):
    items = load(edit)
    if 0 <= i < len(items):
        x = items.pop(i)
        if x["kind"] == "image":
            (Path(edit) / x["src"]).unlink(missing_ok=True)
    return _save(edit, items)


def demo():
    import tempfile
    e = Path(tempfile.mkdtemp(prefix="assets_demo_"))
    png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJ"
           "RU5ErkJggg==")
    assert load(e) == [] and add(e, []) == [] and not (e / "assets.json").exists()
    out = add(e, [{"url": "https://x.com/a/status/1", "say": "  the   launch "}, {"url": "https://github.com/a/b"},
                  {"url": "javascript:alert(1)"}, {"url": "https://x.com/a/status/1"}, {"url": "file:///etc/passwd"},
                  {"image": png, "name": "../chart.png", "say": "chart"}, {"image": "data:text/html;base64,PGI+"}, "junk"])
    assert [x["kind"] for x in out] == ["link", "link", "image"], out
    assert out[0] == {"kind": "link", "url": "https://x.com/a/status/1", "site": "x", "say": "the launch"}, out[0]
    assert out[1]["site"] == "github" and site("https://www.youtube.com/watch?v=1") == "youtube" and site("https://docs.python.org") == "web"
    assert out[2]["src"] == "assets/asset-img1.png" and out[2]["name"] == "chart.png" and (e / out[2]["src"]).is_file()
    assert json.loads((e / "assets.json").read_text(encoding="utf-8"))["items"] == out
    for _ in range(askhook.MAX_IMAGES + 2):
        add(e, [{"image": png}])
    assert sum(x["kind"] == "image" for x in load(e)) == askhook.MAX_IMAGES, "12 images at most"
    big = "data:image/png;base64," + "A" * (askhook.MAX_IMAGE * 4 // 3 + 8)
    n = len(load(e))
    remove(e, 2)
    assert not (e / "assets/asset-img1.png").exists() and len(load(e)) == n - 1
    assert len(add(e, [{"image": big}])) == n - 1, "an image over 15 MB is dropped"
    add(e, [{"url": f"https://example.com/{i}"} for i in range(MAX_LINKS + 5)])
    assert sum(x["kind"] == "link" for x in load(e)) == MAX_LINKS
    for _ in range(len(load(e))):
        remove(e, 0)
    assert load(e) == [] and not (e / "assets.json").exists() and remove(e, 5) == []
    print("assets demo ok")


if __name__ == "__main__":
    if sys.argv[1:2] == ["demo"]:
        demo()
    elif sys.argv[1:2] == ["show"] and len(sys.argv) > 2:
        print(json.dumps({"items": load(sys.argv[2])}, indent=1))
    else:
        print(__doc__)
