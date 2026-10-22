# Container fallback for bagpipe, used when uv is not available on the host.
#
# The image holds only the dependencies, installed from uv.lock. The code runs
# from the checkout, which bagpipe mounts at its own path along with every other
# non-system directory of the host -- so everything here lives under /usr/local,
# where those mounts cannot cover it. Rebuild when uv.lock changes.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/usr/local/bagpipe-env \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1

WORKDIR /usr/local/src/bagpipe
COPY pyproject.toml uv.lock VERSION README.md ./
RUN uv sync --locked --no-install-project \
    && rm -rf /usr/local/src/bagpipe

ENV PATH=/usr/local/bagpipe-env/bin:$PATH
WORKDIR /
CMD ["python", "-m", "bagpipe", "--help"]
