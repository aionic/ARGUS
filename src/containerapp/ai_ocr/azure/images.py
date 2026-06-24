import io
import logging
import os
import tempfile

import fitz  # PyMuPDF
from PIL import Image

logger = logging.getLogger(__name__)


def _render_dpi_matrix() -> "fitz.Matrix":
    """Zoom matrix for page rendering, controlled by env ``IMAGE_RENDER_DPI``.

    PyMuPDF's default pixmap renders at 72 DPI; document scans need ~200 DPI for
    legible page images and to clear the min-resolution quality gate.
    """
    try:
        dpi = float(os.getenv("IMAGE_RENDER_DPI", "200"))
    except (TypeError, ValueError):
        dpi = 200.0
    zoom = max(dpi, 72.0) / 72.0
    return fitz.Matrix(zoom, zoom)


def convert_pdf_into_image(pdf_path):
    """
    Convert PDF pages to PNG images in a temporary directory.
    Returns the temporary directory path containing the images.
    Caller is responsible for cleaning up the temporary directory.
    """
    # Create a temporary directory for the images
    temp_dir = tempfile.mkdtemp(prefix="pdf_images_")

    # Open the PDF file
    pdf_document = None
    try:
        pdf_document = fitz.open(pdf_path)

        # Iterate through all the pages
        render_matrix = _render_dpi_matrix()
        for page_num in range(len(pdf_document)):
            page = pdf_document.load_page(page_num)

            # Convert the page to an image at the configured render DPI
            pix = page.get_pixmap(matrix=render_matrix)

            # Convert the pixmap to bytes
            image_bytes = pix.tobytes("png")

            # Convert the image to a PIL Image object
            image = Image.open(io.BytesIO(image_bytes))

            # Define the output path in the temporary directory
            output_path = os.path.join(temp_dir, f"page_{page_num + 1}.png")

            # Save the image as a PNG file
            image.save(output_path, "PNG")
            logger.debug(f"Saved image: {output_path}")

    except Exception as e:
        logger.error(f"Error converting PDF to images: {e}")
        raise
    finally:
        # Ensure PDF document is properly closed
        if pdf_document:
            pdf_document.close()

    return temp_dir
