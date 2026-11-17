"""Run with python3 test_pdf_to_folio.py; requires pdfseparate and pdfinfo."""

from pathlib import Path
import subprocess
import tempfile

from pdf_to_folio import convert_pdf


def check():
    # A two-page PDF with distinct page sizes verifies splitting and order.
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 300] /Resources << >> >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 500] /Resources << >> >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "Sample folio.PDF"
        source.write_bytes(pdf)
        folio = convert_pdf(source, root / "folios")
        assert folio.name == "Sample folio"
        assert sorted(p.name for p in folio.iterdir()) == ["page-0001.pdf", "page-0002.pdf"]
        for number, size in [(1, "200 x 300"), (2, "400 x 500")]:
            info = subprocess.check_output(["pdfinfo", str(folio / f"page-{number:04d}.pdf")], text=True)
            assert size in info
            assert next(line for line in info.splitlines() if line.startswith("Pages:")).split()[-1] == "1"
        try:
            convert_pdf(source, root / "folios")
        except FileExistsError:
            pass
        else:
            raise AssertionError("Existing folio was overwritten")
        assert source.read_bytes() == pdf
    print("Folio conversion check passed")


if __name__ == "__main__":
    check()
