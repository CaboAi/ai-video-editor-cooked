"""The ink mask the render gates share: anything off the frame's own modal ground (the card's flat ground)."""
import numpy as np


def plate(video, vf, w, h):
    """A painted ground plate. Clips here have flat grounds, so always None (the modal-luma mask)."""
    return None


def mask(f, ground, delta):
    """Ink: off the plate when there is one, else off the frame's modal ground by `delta`."""
    if ground is not None:
        return np.abs(f.astype(int) - ground) > delta
    g = np.bincount(f.ravel(), minlength=256).argmax()
    return np.abs(f.astype(int) - int(g)) > delta
