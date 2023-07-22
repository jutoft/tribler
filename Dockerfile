# libtorrent-1.2.9 does not support python 3.11 yet
FROM python:3.10

RUN apt-get update \
    && apt-get install -y libsodium23 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -ms /bin/bash user
USER user
WORKDIR /home/user

# Then, install pip dependencies so that it can be cached and does not
# need to be built every time the source code changes.
# This reduces the docker build time.
RUN mkdir requirements
COPY ./requirements-core.txt requirements/core-requirements.txt
RUN pip3 install -r requirements/core-requirements.txt

# Copy the source code and set the working directory
COPY ./src tribler/src/
WORKDIR /home/user/tribler

# Set to -1 to use the default
ENV CORE_API_PORT=20100
ENV IPV8_PORT=7759
ENV TORRENT_PORT=-1
ENV DOWNLOAD_DIR=/downloads
ENV TSTATEDIR=/state

VOLUME /state
VOLUME /downloads

# Only run the core process with --core switch
CMD exec python3 /home/user/tribler/src/run_tribler_headless.py --restapi=${CORE_API_PORT} --ipv8=${IPV8_PORT} --libtorrent=${TORRENT_PORT} "--downloaddir=${DOWNLOAD_DIR}"
