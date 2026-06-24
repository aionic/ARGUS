# PaddleOCR quality-probe service (CPU).
#
# We base on the official PaddlePaddle CPU image rather than installing the
# paddlepaddle wheel on a slim base: the wheel segfaults (exit 139) on some CPUs,
# whereas the official image is built and tested to run Paddle on CPU. paddleocr is
# layered on top (it does NOT pull paddlepaddle itself). Models are baked at build
# time so the container needs no network egress at runtime (VNet / scale-to-zero).
FROM paddlepaddle/paddle:2.6.2

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# paddleocr + the web stack (paddlepaddle is already present in the base image).
RUN pip install --no-cache-dir \
        "paddleocr==2.7.3" \
        "numpy==1.26.4" \
        "fastapi==0.111.0" \
        "uvicorn[standard]==0.30.1" \
        "python-multipart==0.0.9" \
        "pillow==10.3.0"

# Bake the PP-OCRv4 English detection/recognition/angle models into the image.
RUN python -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='en', show_log=False)"

COPY app.py ./

EXPOSE 8000

# Single worker: PaddleOCR holds models in-process and access is serialised with a
# lock; scale horizontally via Container Apps replicas, not in-process workers.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
