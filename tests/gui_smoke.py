"""GUI smoke test. Run under a display:  xvfb-run -a python tests/gui_smoke.py <workdir>"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from pdf2jpg import gui  # noqa: E402
from pdf2jpg.logger import setup_logging  # noqa: E402

setup_logging()
work = Path(sys.argv[1])
work.mkdir(parents=True, exist_ok=True)


def mk(name, pages):
    ims = []
    for i in range(pages):
        im = Image.new("RGB", (595, 842), "white")
        ImageDraw.Draw(im).text((40, 40), f"{name} {i + 1}", fill="black")
        ims.append(im)
    p = work / name
    ims[0].save(p, "PDF", save_all=True, append_images=ims[1:], resolution=72)
    return p


pdfs = [mk("alpha.pdf", 3), mk("beta.pdf", 2)]
bad = work / "bad.pdf"
bad.write_bytes(b"%PDF-1.4 garbage")
pdfs.append(bad)

app = gui.App()
print("dnd_ok:", app._dnd_ok)
app.out_var.set(str(work / "out"))
app.update()

# simulate a drop event payload with a space-containing path in braces, like tkdnd sends
payload = " ".join("{" + str(p) + "}" for p in pdfs) + " " + str(pdfs[0])  # includes a duplicate
class E:  # minimal event
    data = payload
app._on_drop(E())
assert len(app._files) == 3, app._files
app.update()

# make the toast/messagebox non-blocking for the test
gui.messagebox.showwarning = lambda *a, **k: print("WARNING DIALOG:", a[1][:200].replace("\n", " | "))
app.quality_var.set("Standard - 150 DPI")
app._update_hint()
app._start()

deadline = time.time() + 60
shot_taken = False
while app._running and time.time() < deadline:
    app.update()
    time.sleep(0.02)
    if not shot_taken and app._toasts:
        app.update()
        time.sleep(0.2)
        app.update()
        try:
            from PIL import ImageGrab
            ImageGrab.grab(xdisplay=None).save(work / "screenshot_toast.png")
            shot_taken = True
        except Exception as exc:
            print("screenshot failed:", exc)
for _ in range(20):
    app.update(); time.sleep(0.02)

assert not app._running, "job did not finish"
out = work / "out"
roots = [p for p in out.iterdir() if p.name.startswith("PDF_Batch_")]
print("batch roots:", [r.name for r in roots])
print("subfolders:", sorted(p.name for p in roots[0].iterdir()))
print("alpha pages:", sorted(p.name for p in (roots[0] / "alpha").iterdir()))
print("progress label:", app.lbl_progress.cget("text"))
print("progress value:", app.progress.get())
print("buttons idle:", app.btn_convert.cget("state"), app.btn_cancel.cget("state"))

try:
    from PIL import ImageGrab
    ImageGrab.grab(xdisplay=None).save(work / "screenshot_final.png")
except Exception as exc:
    print("screenshot failed:", exc)
app._on_close()
print("GUI SMOKE OK")
