# ARGUS PaddleOCR quality-probe service

A self-hosted [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) microservice used
as a **cheap legibility pre-gate** in the ARGUS pipeline. It runs *before* Azure
Document Intelligence / Content Understanding and returns aggregate per-line
recognition confidence so the backend can short-circuit (skip the paid extraction
call) on scans that are too low-quality to extract.

This service does **not** perform document extraction. It is a quality probe only.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/health` | Liveness/readiness (`model_loaded`). |
| `POST` | `/assess` | multipart `file` = a rendered page image (PNG/JPG). Returns `{n_lines, mean, min, p10, frac_low, word_min, elapsed_ms}`. Optional `word_min` query param. |

If `SERVICE_KEY` is set, callers must send a matching `X-Service-Key` header.

## Confidence stats

`mean` is the average per-line recognition score; `frac_low` is the fraction of lines
scoring below `word_min` (default 0.70). The backend flags a document when
`mean < PADDLE_CONFIDENCE_MEAN_MIN` or `frac_low > PADDLE_CONFIDENCE_LOW_FRAC_MAX`.

## Environment

| Var | Default | Meaning |
|-----|---------|---------|
| `PADDLE_LANG` | `en` | OCR language. |
| `PADDLE_USE_ANGLE_CLS` | `true` | Enable angle classification (skewed scans). |
| `PADDLE_WORD_MIN` | `0.70` | Default low-confidence line threshold. |
| `SERVICE_KEY` | _(empty)_ | Optional shared-key auth. |

## Build & run locally

```bash
docker build -t argus-paddleocr ./src/paddleocr-service
docker run -p 8000:8000 argus-paddleocr
curl -F file=@page.png http://localhost:8000/assess
```

Models are baked into the image at build time, so no runtime network egress is needed.
