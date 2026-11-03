# Third-party software

This project's own code is MIT-licensed (see LICENSE). It uses the following components.

| Component | Use | License |
|---|---|---|
| [Poppler](https://poppler.freedesktop.org/) (Windows build by oschwartz10612/poppler-windows) | renders PDF pages (`pdftoppm`, `pdfinfo`), run as a separate program | GPL-2.0 / GPL-3.0 |
| [pdf2image](https://github.com/Belval/pdf2image) | Python wrapper around Poppler | MIT |
| [Pillow](https://python-pillow.org/) | JPEG encoding | HPND |
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) | GUI | MIT |
| [tkinterdnd2](https://github.com/Eliav2/tkinterdnd2) | drag & drop | MIT |
| [PyInstaller](https://pyinstaller.org/) | builds the .exe | GPL-2.0 with bootloader exception |

## Poppler in the released .exe

The released `PDF2JPG.exe` embeds unmodified Poppler binaries. Poppler is free software under the GNU GPL; its
license texts and complete source code are available from the Poppler project and from the
poppler-windows release page used to build this executable. Replace the Poppler files in the `poppler/` folder
and rebuild to use a different version.
